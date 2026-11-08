"""Deck for Windows：语音小助手（11-08 第三步），代替 Mac 上的 DeckVoice.app（manager/voice/helper/，Swift）。

和 DeckVoice 一样：唯一碰麦克风和扬声器的地方，通过 TCP 127.0.0.1:8779 连语音服务，帧格式见 manager/voice/INTERFACE.md：
  A  helper→服务  16 kHz 单声道 float32 小端，约 100ms 一帧
  J  双向 JSON
  P  服务→helper  要播的 PCM（前 4 字节 uint32 LE 采样率，后面 float32 小端）

跟 Mac 版的区别：
- 麦克风 / 扬声器用 sounddevice（PortAudio，默认走 Windows 的 MME「声音映射器」，跟着系统默认设备，采样率由系统转换）
- 没有 Apple 实时字幕：不发 partial / apple_final，服务直接用 SenseVoice 定稿（asr.py 本来就这么兜底）
- 没有回声消除：连上就报 {"t":"aec","on":false}，收到 vp 也回 false。服务走「没回声消除」那条路
  （echo_tail_s 等回声散了再开接话窗口；说话中听到「停」要核对声纹；开口打断关闭，见 stopword.py）
- 全局快捷键：Ctrl+Alt+M 麦克风开/关，Ctrl+Alt+J 直接说（按住超过 0.6 秒 = 按住说话）。
  「停」的 Ctrl+Alt+. 不在这里注册：Windows 上它是电脑操控的备用急停（winport/hands_win.py，Ctrl+Alt+Esc / Ctrl+Alt+.），
  全局快捷键谁先注册谁拿到，不能让语音抢走急停；由急停那边顺手让语音也停（POST 8778/stop）。
- 没有菜单栏图标；麦克风开关记在 <语音目录>/helper-win.json

跑在哪：由 voice_launch.py 在语音服务进程（Deck.exe --voice）里起一个线程。也可以单独跑：
  python windows/voice_helper.py [--port 8779]
没有声卡（云电脑）时不会崩：麦克风报 no_device，播放按时间线空转，照样回 playback_done。
"""
import collections
import json
import os
import queue
import socket
import struct
import sys
import threading
import time

import numpy as np

SR = 16000
BLOCK = SR // 10            # 100ms 一帧
IS_WIN = sys.platform == "win32"
DUCK_LEVEL = 0.4            # 「让一下」：她的音量压到 40%
HOLD_FADE_S = 0.06          # 暂停前淡出约 60ms，不「咔」一声
DUCK_FADE_S = 0.08


_SD = []


def _sd():
    """sounddevice 可能装不上（Linux 没有 libportaudio）或没有声卡：返回 None，不让整个助手起不来。只试一次。"""
    if not _SD:
        try:
            import sounddevice as sd  # noqa: PLC0415
            _SD.append(sd)
        except Exception as e:  # noqa: BLE001  OSError: PortAudio library not found 等
            print("[helper-win] sounddevice 不可用：%s" % e, flush=True)
            _SD.append(None)
    return _SD[0]


def resample(x, sr_in, sr_out):
    """线性插值换采样率（语音够用；TTS 是 24k，声卡多半 48k / 44.1k）。"""
    x = np.asarray(x, dtype=np.float32)
    if sr_in == sr_out or len(x) == 0:
        return x
    n = max(1, int(round(len(x) * sr_out / sr_in)))
    t = np.arange(n, dtype=np.float64) * (sr_in / sr_out)
    return np.interp(t, np.arange(len(x)), x).astype(np.float32)


# ---------------------------------------------------------------- 播放

class Player:
    """播放队列：P 帧排队，play_mark 排在中间，播到那里回 playback_done。stop 立刻清空。
    hold：淡出后原地暂停（排队的都留着），放开淡入接着说；duck：压到 40% 不停。
    声卡打不开时用一个线程按真实时间「空放」，时间线和回执照样对。"""

    def __init__(self, on_mark, log=print):
        self.on_mark = on_mark
        self.log = log
        self.lock = threading.Lock()
        self.items = collections.deque()   # ["pcm", 数组, 已播位置] 或 ["mark", id]
        self.gain = 1.0
        self.target = 1.0
        self.held = False
        self.paused = False
        self.ducked = False
        self.rate = 48000
        self.stream = None
        self.marks = queue.Queue()
        self._closed = False
        threading.Thread(target=self._mark_loop, name="helper-marks", daemon=True).start()

    def open(self):
        sd = _sd()
        if sd is not None:
            try:
                dev = sd.query_devices(kind="output")
                rate = int(dev.get("default_samplerate") or 48000)
                self.rate = rate
                self.stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32",
                                              blocksize=int(rate * 0.02), callback=self._cb)
                self.stream.start()
                self.log("[helper-win] 扬声器：%s（%d Hz）" % (dev.get("name"), rate))
                return True
            except Exception as e:  # noqa: BLE001
                self.stream = None
                self.log("[helper-win] 扬声器打不开（%s），按时间线空放" % e)
        threading.Thread(target=self._clock, name="helper-clock", daemon=True).start()
        return False

    def close(self):
        self._closed = True
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:  # noqa: BLE001
                pass

    # —— 服务发来的命令 ——
    def enqueue(self, sr, pcm):
        pcm = resample(pcm, sr, self.rate)
        with self.lock:
            self.items.append(["pcm", pcm, 0])

    def mark(self, mid):
        with self.lock:
            self.items.append(["mark", mid])

    def stop(self):
        with self.lock:
            self.items.clear()          # 排着的块和回执一起丢（和 DeckVoice 一样，被停掉的不回 playback_done）
            self.held = self.paused = self.ducked = False
            self.gain = self.target = 1.0

    def hold(self, on):
        with self.lock:
            if on == self.held:
                return
            self.held = on
            if on:
                self.target = 0.0       # 从当前音量（可能已被 duck 压低）淡出，淡完暂停
            else:
                self.paused = False
                self.ducked = False
                self.gain, self.target = 0.0, 1.0

    def duck(self, on):
        with self.lock:
            if self.held:
                return
            self.ducked = on
            self.target = DUCK_LEVEL if on else 1.0

    def pending(self):
        with self.lock:
            return sum(len(it[1]) - it[2] for it in self.items if it[0] == "pcm") / self.rate

    # —— 取数据（声卡回调或空放线程调）——
    def pull(self, n):
        out = np.zeros(n, dtype=np.float32)
        marks = []
        with self.lock:
            if not self.paused:
                i = 0
                while self.items and (i < n or self.items[0][0] == "mark"):
                    it = self.items[0]
                    if it[0] == "mark":
                        self.items.popleft()
                        marks.append(it[1])
                        continue
                    pcm, pos = it[1], it[2]
                    k = min(n - i, len(pcm) - pos)
                    out[i:i + k] = pcm[pos:pos + k]
                    it[2] += k
                    i += k
                    if it[2] >= len(pcm):
                        self.items.popleft()
                g0, tgt = self.gain, self.target
                if g0 != tgt or g0 != 1.0:
                    fade = HOLD_FADE_S if self.held else DUCK_FADE_S
                    step = n / (self.rate * fade)
                    g1 = tgt if abs(tgt - g0) <= step else g0 + step * (1 if tgt > g0 else -1)
                    out *= np.linspace(g0, g1, n, dtype=np.float32)
                    self.gain = g1
                    if self.held and g1 <= 0.0:
                        self.paused = True
        for m in marks:
            self.marks.put(m)
        return out

    def _cb(self, outdata, frames, _t, _status):
        try:
            outdata[:, 0] = self.pull(frames)
        except Exception:  # noqa: BLE001  回调里出错只能静音，不能抛
            outdata.fill(0)

    def _clock(self):
        step = 0.02
        nxt = time.monotonic()
        while not self._closed and self.stream is None:
            self.pull(int(self.rate * step))
            nxt += step
            time.sleep(max(0.0, nxt - time.monotonic()))

    def _mark_loop(self):
        while True:
            m = self.marks.get()
            try:
                self.on_mark(m)
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------- 麦克风

class Mic:
    """16 kHz 单声道 float32，每 100ms 交一块给 on_audio。设备不支持 16k 时按它的默认采样率开、自己换成 16k。"""

    def __init__(self, on_audio, log=print):
        self.on_audio = on_audio
        self.log = log
        self.stream = None
        self.reason = ""
        self.lock = threading.Lock()

    @property
    def running(self):
        return self.stream is not None

    def start(self):
        with self.lock:
            if self.stream is not None:
                return True
            sd = _sd()
            if sd is None:
                self.reason = "no_device"
                return False
            try:
                dev = sd.query_devices(kind="input")
            except Exception as e:  # noqa: BLE001
                self.reason = "no_device"
                self.log("[helper-win] 没有麦克风：%s" % e)
                return False
            last = None
            for rate in (SR, int(dev.get("default_samplerate") or 48000)):
                try:
                    blk = rate // 10

                    def cb(indata, _frames, _t, _status, _rate=rate):
                        x = indata[:, 0].astype(np.float32, copy=True)
                        self.on_audio(resample(x, _rate, SR) if _rate != SR else x)

                    s = sd.InputStream(samplerate=rate, channels=1, dtype="float32", blocksize=blk, callback=cb)
                    s.start()
                    self.stream = s
                    self.reason = ""
                    self.log("[helper-win] 麦克风：%s（%d Hz）" % (dev.get("name"), rate))
                    return True
                except Exception as e:  # noqa: BLE001
                    last = e
            msg = str(last or "")
            # 「设置 → 隐私和安全性 → 麦克风」关掉时 PortAudio 报拒绝访问 / 设备不可用
            self.reason = "no_permission" if any(k in msg.lower() for k in ("access", "denied", "unanticipated host")) else "no_device"
            self.log("[helper-win] 麦克风打不开：%s" % msg)
            return False

    def stop(self):
        with self.lock:
            s, self.stream = self.stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------- 全局快捷键

HOTKEYS = (("mic", 0x4D), ("talk", 0x4A))   # Ctrl+Alt+M / Ctrl+Alt+J（Ctrl+Alt+. 留给急停，见文件开头）
HOTKEY_TEXT = {"mic": "Ctrl+Alt+M", "talk": "Ctrl+Alt+J"}


def hotkey_loop(on_key, log=print):
    """RegisterHotKey 不需要任何权限；被别的软件占了就跳过那一个。on_key(name, phase)：phase = press / hold / release。
    「直接说」按住超过 0.6 秒算按住说话（和 Mac 的 ⌥⌘J 一样），松手马上出结果。"""
    if not IS_WIN:
        return
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415
    u32 = ctypes.windll.user32
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x0312
    ok = {}
    for i, (name, vk) in enumerate(HOTKEYS, start=1):
        ok[i] = (name, vk)
        if not u32.RegisterHotKey(None, i, MOD_ALT | MOD_CONTROL | MOD_NOREPEAT, vk):
            log("[helper-win] %s 被别的软件占了，这个快捷键不可用" % HOTKEY_TEXT[name])
            ok.pop(i)
    if not ok:
        return

    def watch_hold(vk):
        t0 = time.monotonic()
        held = False
        while u32.GetAsyncKeyState(vk) & 0x8000:
            if not held and time.monotonic() - t0 > 0.6:
                held = True
                on_key("talk", "hold")
            time.sleep(0.03)
        if held:
            on_key("talk", "release")

    msg = wintypes.MSG()
    while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message != WM_HOTKEY or msg.wParam not in ok:
            continue
        name, vk = ok[msg.wParam]
        try:
            on_key(name, "press")
            if name == "talk":
                threading.Thread(target=watch_hold, args=(vk,), name="helper-talk-hold", daemon=True).start()
        except Exception as e:  # noqa: BLE001
            log("[helper-win] 快捷键出错：%s" % e)


# ---------------------------------------------------------------- 和服务的通道

class Helper:
    def __init__(self, port=8779, state_dir=None, log=None, hotkeys=True, audio=True):
        self.port = port
        self.log = log or (lambda s: print(s, flush=True))
        self.state_file = os.path.join(state_dir, "helper-win.json") if state_dir else None
        self.mic_on = self._load().get("mic", True)   # 他想不想开（Ctrl+Alt+M 切换，记住）
        self.sock = None
        self.wlock = threading.Lock()
        self.audio_q = queue.Queue(maxsize=50)        # 约 5 秒；服务没连上 / 卡住时丢最旧的
        self.player = Player(self._on_mark, self.log)
        self.mic = Mic(self._on_audio, self.log)
        self.use_audio = audio
        self.use_hotkeys = hotkeys
        self.stopped = threading.Event()
        self.connected = threading.Event()

    # —— 记住麦克风开关 ——
    def _load(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, TypeError):
            return {}

    def _save(self):
        if not self.state_file:
            return
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"mic": self.mic_on}, f)
            os.replace(tmp, self.state_file)
        except OSError:
            pass

    # —— 发 ——
    def send(self, kind, payload):
        s = self.sock
        if s is None:
            return False
        try:
            with self.wlock:
                s.sendall(kind + struct.pack(">I", len(payload)) + payload)
            return True
        except OSError:
            return False

    def send_json(self, obj):
        return self.send(b"J", json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _on_audio(self, x):
        if self.sock is None:
            return
        try:
            self.audio_q.put_nowait(x)
        except queue.Full:
            try:
                self.audio_q.get_nowait()
                self.audio_q.put_nowait(x)
            except queue.Empty:
                pass

    def _audio_loop(self):
        while not self.stopped.is_set():
            try:
                x = self.audio_q.get(timeout=1)
            except queue.Empty:
                continue
            self.send(b"A", np.asarray(x, dtype="<f4").tobytes())

    def _on_mark(self, mid):
        self.send_json({"t": "playback_done", "id": mid})

    # —— 麦克风 ——
    def apply_mic(self, announce=True):
        if self.mic_on and self.use_audio:
            self.mic.start()
        else:
            self.mic.stop()
        on = self.mic.running
        if announce:
            m = {"t": "mic", "on": on}
            if self.mic_on and not on:
                m["reason"] = self.mic.reason or "no_device"
                m["apps"] = []
            self.send_json(m)

    def set_mic(self, on):
        self.mic_on = bool(on)
        self._save()
        self.apply_mic(True)

    # —— 收 ——
    def handle(self, m):
        t = m.get("t")
        if t == "mic":
            self.set_mic(m.get("on", True))
        elif t == "status_request":
            self.apply_mic(True)
        elif t == "stop":
            self.player.stop()
        elif t == "hold":
            self.player.hold(bool(m.get("on")))
        elif t == "duck":
            self.player.duck(bool(m.get("on")))
        elif t == "play_mark":
            self.player.mark(m.get("id"))
        elif t == "vp":
            # Windows 版没有回声消除：照实回报，服务就按「没回声消除」处理
            self.send_json({"t": "aec", "on": False})
        # listen（开/关 Apple 实时字幕）：Windows 没有，忽略

    def on_hotkey(self, name, phase="press"):
        if phase == "hold":
            self.send_json({"t": "hotkey", "name": "talk_hold"})
            return
        if phase == "release":
            self.send_json({"t": "hotkey", "name": "talk_release"})
            return
        if name == "mic":
            self.set_mic(not self.mic_on)
        if name == "talk" and not self.mic.running:
            self.send_json({"t": "log", "text": "麦克风现在关着，Ctrl+Alt+J 没法听"})
        self.send_json({"t": "hotkey", "name": name})
        if name == "stop":
            self.player.stop()  # 不等服务，先让声音停

    def _recv_exact(self, s, n):
        buf = bytearray()
        while len(buf) < n:
            b = s.recv(n - len(buf))
            if not b:
                raise ConnectionError("服务断开")
            buf += b
        return bytes(buf)

    def _session(self, s):
        self.sock = s
        self.connected.set()
        self.log("[helper-win] 已连上语音服务 127.0.0.1:%d" % self.port)
        self.send_json({"t": "hello", "platform": "win", "aec": False, "captions": False,
                        "hotkeys": HOTKEY_TEXT if (IS_WIN and self.use_hotkeys) else {}})
        self.send_json({"t": "aec", "on": False})
        self.apply_mic(True)
        while not self.stopped.is_set():
            head = self._recv_exact(s, 5)
            kind, n = head[:1], struct.unpack(">I", head[1:])[0]
            if n > 64 * 1024 * 1024:
                raise ConnectionError("帧太大 %d" % n)
            payload = self._recv_exact(s, n) if n else b""
            if kind == b"P" and len(payload) > 4:
                sr = struct.unpack("<I", payload[:4])[0]
                if sr > 0:
                    self.player.enqueue(sr, np.frombuffer(payload[4:], dtype="<f4"))
            elif kind == b"J":
                try:
                    self.handle(json.loads(payload.decode("utf-8")))
                except Exception as e:  # noqa: BLE001
                    self.log("[helper-win] 处理命令出错：%s" % e)

    def run(self):
        """连服务，断了每秒重连；直到 stop()。"""
        self.player.open()
        threading.Thread(target=self._audio_loop, name="helper-audio", daemon=True).start()
        if self.use_hotkeys and IS_WIN:
            threading.Thread(target=hotkey_loop, args=(self.on_hotkey, self.log), name="helper-hotkeys",
                             daemon=True).start()
        quiet = False
        while not self.stopped.is_set():
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=3)
            except OSError:
                if not quiet:
                    self.log("[helper-win] 语音服务还没起来，每秒重试")
                    quiet = True
                self.stopped.wait(1)
                continue
            quiet = False
            try:
                s.settimeout(None)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self._session(s)
            except (ConnectionError, OSError) as e:
                self.log("[helper-win] 和语音服务断开：%s" % e)
            finally:
                self.sock = None
                self.connected.clear()
                self.mic.stop()          # 断开时不占着麦克风（Windows 任务栏不会一直亮着「正在使用麦克风」）
                self.player.stop()
                try:
                    s.close()
                except OSError:
                    pass
            self.stopped.wait(1)

    def stop(self):
        self.send_json({"t": "quit"})
        self.stopped.set()
        s = self.sock
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.mic.stop()
        self.player.close()


def start_thread(port=8779, state_dir=None, log=None, **kw):
    """语音服务进程里调：起一个后台线程跑助手，返回 Helper。"""
    h = Helper(port, state_dir, log, **kw)
    threading.Thread(target=h.run, name="voice-helper", daemon=True).start()
    return h


def main(argv=None):
    import argparse  # noqa: PLC0415
    ap = argparse.ArgumentParser(description="Deck 语音小助手（Windows）")
    ap.add_argument("--port", type=int, default=8779)
    ap.add_argument("--state-dir", default=None)
    a = ap.parse_args(argv)
    h = Helper(a.port, a.state_dir)
    try:
        h.run()
    except KeyboardInterrupt:
        h.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
