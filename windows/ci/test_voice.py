"""检查 Windows 版语音层（11-08 第三步）：语音服务能起来、小助手协议对、朗读有声或干净地报「缺模型」、模型下载能续传能校验。

用法：python windows/ci/test_voice.py（Windows 上跑全部检查；Linux / Mac 上加 DECK_OS=win 也能跑，只是不碰真声卡）。
- 语音服务用临时目录（DECK_VOICE_ROOT）和随机端口跑，不碰本机 8778 / 8779，也不碰真的语音数据
- 不联网：模型下载用本机临时 HTTP 服务模拟（支持 Range 续传）
- 有语音模型时（DECK_TEST_VOICE_MODELS 指向一个 models 目录）会真的合成一句、检查收到 P 帧
- 3 分钟内一定结束：超时直接 os._exit
失败时退出码不是 0。
"""
import bz2
import hashlib
import http.client
import http.server
import io
import json
import os
import queue
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOWS = os.path.dirname(HERE)
REPO = os.path.dirname(WINDOWS)
MANAGER = os.path.join(REPO, "manager")
sys.path.insert(0, WINDOWS)
IS_WIN = sys.platform == "win32"
DEADLINE = 170

FAIL = []
PASS = [0]
PROCS = []


def check(ok, what):
    if ok:
        PASS[0] += 1
    else:
        FAIL.append(what)
        print("失败：" + what, flush=True)


def _hard_timeout():
    print("失败：超过 %d 秒还没跑完，强制结束" % DEADLINE, flush=True)
    for p in PROCS:
        try:
            p.kill()
        except OSError:
            pass
    os._exit(3)


threading.Timer(DEADLINE, _hard_timeout).start()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ================================================================ 1. 模型下载（本机模拟服务器）

class RangeHandler(http.server.BaseHTTPRequestHandler):
    """只发文件，支持 Range（续传）。记下收到过哪些 Range 头。"""
    root = ""
    ranges = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = os.path.join(self.root, self.path.lstrip("/").replace("/", os.sep))
        if not os.path.isfile(path):
            self.send_error(404)
            return
        data = open(path, "rb").read()
        rng = self.headers.get("Range")
        RangeHandler.ranges.append(rng)
        if rng and rng.startswith("bytes="):
            start = int(rng[6:].split("-")[0])
            if start >= len(data):
                self.send_response(416)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = data[start:]
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, len(data) - 1, len(data)))
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_models(tmp):
    import voice_models as vm

    srv_root = os.path.join(tmp, "srv")
    os.makedirs(os.path.join(srv_root, "m"))
    # 一个单文件模型
    blob = os.urandom(300_000)
    open(os.path.join(srv_root, "m", "one.onnx"), "wb").write(blob)
    # 一个压缩包模型：top/ 下有要的、不要的文件，外加一个想逃出目录的坏条目
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in (("pkg-1/model.int8.onnx", os.urandom(200_000)), ("pkg-1/tokens.txt", b"a 1\n"),
                           ("pkg-1/big-unused.onnx", os.urandom(50_000)), ("pkg-1/sub/x.txt", b"x"),
                           ("../evil.txt", b"evil")):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    tarb = bz2.compress(buf.getvalue())
    open(os.path.join(srv_root, "m", "pkg-1.tar.bz2"), "wb").write(tarb)

    items = {
        "one": {"label": "单文件", "url": "m/one.onnx", "kind": "file", "size": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(), "dest": "one/one.onnx", "need": []},
        "pkg": {"label": "压缩包", "url": "m/pkg-1.tar.bz2", "kind": "tar", "size": len(tarb),
                "sha256": hashlib.sha256(tarb).hexdigest(), "top": "pkg-1", "dest": "pkg",
                "keep": ["model.int8.onnx", "tokens.txt", "sub/*"], "need": ["model.int8.onnx", "tokens.txt"]},
        "bad": {"label": "校验和不对", "url": "m/one.onnx", "kind": "file", "size": len(blob),
                "sha256": "0" * 64, "dest": "bad.onnx", "need": []},
    }
    packs = {"p": {"label": "测试包", "items": ["one", "pkg"]}, "b": {"label": "坏包", "items": ["bad"]}}

    RangeHandler.root = srv_root
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    root = os.path.join(tmp, "dl-root")
    mdir = vm.models_dir(root)
    st = vm.status(root, items, packs)
    check(st["packs"]["p"]["installed"] is False and st["packs"]["p"]["download_bytes"] == len(blob) + len(tarb),
          "status() 应该报测试包没装、要下 %d 字节" % (len(blob) + len(tarb)))

    # 先放半截 .part，模拟上次下到一半断了：这次应该带 Range 接着下
    os.makedirs(os.path.join(mdir, ".download"), exist_ok=True)
    open(os.path.join(mdir, ".download", "one.onnx.part"), "wb").write(blob[:120_000])
    seen = []
    r = vm.download(["p"], root, seen.append, items=items, pack_map=packs, base=base)
    check(r.get("ok") and sorted(r.get("installed", [])) == ["one", "pkg"], "下载测试包应该成功：%r" % r)
    check("bytes=120000-" in RangeHandler.ranges, "半截文件应该用 Range 续传（收到的 Range：%r）" % RangeHandler.ranges)
    check(open(os.path.join(mdir, "one", "one.onnx"), "rb").read() == blob, "续传后的文件内容应该完整")
    check(os.path.isfile(os.path.join(mdir, "pkg", "model.int8.onnx")) and os.path.isfile(os.path.join(mdir, "pkg", "sub", "x.txt")),
          "压缩包里要的文件应该解出来")
    check(not os.path.exists(os.path.join(mdir, "pkg", "big-unused.onnx")), "不要的文件不应该解出来")
    check(not os.path.exists(os.path.join(root, "evil.txt")) and not os.path.exists(os.path.join(mdir, "evil.txt")),
          "压缩包里 ../ 的条目不能解出去")
    check(not os.path.exists(os.path.join(mdir, ".download", "pkg-1.tar.bz2.part")), "解完的压缩包应该清掉")
    phases = {d["phase"] for d in seen}
    check({"verify", "extract", "done"} <= phases, "进度回调应该报 verify / extract / done：%r" % phases)
    check(all(d["bytes_done"] <= d["bytes_total"] for d in seen), "进度不能超过总量")
    check(vm.status(root, items, packs)["packs"]["p"]["installed"], "下完后 status() 应该报已装")
    r2 = vm.download(["p"], root, None, items=items, pack_map=packs, base=base)
    check(r2.get("ok") and not r2.get("installed") and sorted(r2.get("skipped")) == ["one", "pkg"], "已装的不该重下：%r" % r2)

    t0 = time.time()
    rb = vm.download(["b"], root, None, items=items, pack_map=packs, base=base)
    check(not rb.get("ok") and "校验" in (rb.get("error") or ""), "校验和不对应该报错：%r" % rb)
    check(not os.path.exists(os.path.join(mdir, "bad.onnx")), "校验不对的文件不能装上")
    check(time.time() - t0 < 60, "校验失败的重试不该太久")
    r404 = vm.download(["one"], os.path.join(tmp, "dl-404"), None, items=dict(items, one=dict(items["one"], url="m/nope")),
                       base=base)
    check(not r404.get("ok") and r404.get("error"), "404 应该报错不崩：%r" % r404)
    httpd.shutdown()

    # 真的模型清单：名字要和语音服务找的目录一致
    for n, it in vm.ITEMS.items():
        check(it["size"] > 0 and len(it.get("sha256", "")) == 64 and it["url"].startswith(("asr-models/", "kws-models/", "tts-models/", "speaker-recongition-models/")),
              "模型 %s 的清单不完整" % n)
    check(vm.ITEMS["kokoro"]["dest"] == "kokoro-multi-lang-v1_1", "Kokoro 要装到 tts.py 找的 kokoro-multi-lang-v1_1")
    check(vm.ITEMS["silero_vad"]["dest"] == "silero_vad/silero_vad.onnx", "VAD 要装到 silero_vad/silero_vad.onnx")
    check(set(vm.DEFAULT_PACKS) == {"wake", "asr", "tts"}, "默认下载 wake / asr / tts 三个包")


# ================================================================ 2. 播放队列（不碰声卡）

def test_player():
    import numpy as np
    import voice_helper as vh

    got = []
    p = vh.Player(got.append, log=lambda s: None)
    p.rate = 48000
    p.enqueue(24000, np.ones(12000, np.float32) * 0.5)    # 0.5 秒，24k → 48k
    check(abs(p.pending() - 0.5) < 0.01, "24k 的 0.5 秒换到 48k 后应该还是 0.5 秒：%.3f" % p.pending())
    p.mark("m1")
    out = p.pull(4800)
    check(abs(float(out.max()) - 0.5) < 1e-3, "播放出来的音量应该不变")
    p.pull(48000)
    time.sleep(0.2)
    check(got == ["m1"], "播到 play_mark 应该回 playback_done：%r" % got)
    # duck：压到 40%
    p.enqueue(48000, np.ones(48000, np.float32))
    p.duck(True)
    p.pull(9600)
    check(abs(float(p.pull(960).max()) - vh.DUCK_LEVEL) < 1e-3, "duck 后音量应该是 40%")
    # hold：淡出后原地暂停，排队的都还在
    p.hold(True)
    p.pull(4800)
    left = p.pending()
    z = p.pull(4800)
    check(p.paused and float(np.abs(z).max()) == 0.0 and abs(p.pending() - left) < 1e-6, "hold 后应该暂停且不丢队列")
    p.duck(True)
    check(p.held, "暂停中 duck 应该被忽略")
    p.hold(False)
    p.pull(9600)
    check(abs(float(p.pull(960).max()) - 1.0) < 1e-3, "放开 hold 应该淡入回到原音量")
    # stop：立刻清空，排着的回执也丢掉
    p.mark("m2")
    p.stop()
    check(p.pending() == 0.0 and not p.items, "stop 应该立刻清空播放队列")
    p.pull(4800)
    time.sleep(0.1)
    check(got == ["m1"], "stop 掉的 play_mark 不该回执")
    check(len(vh.resample(np.zeros(1600, np.float32), 16000, 48000)) == 4800, "16k → 48k 长度应该 ×3")


# ================================================================ 3. 语音服务 + 假小助手

class SSE:
    def __init__(self, port):
        self.q = queue.Queue()
        self.ok = threading.Event()
        threading.Thread(target=self._run, args=(port,), daemon=True).start()

    def _run(self, port):
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            c.request("GET", "/events")
            r = c.getresponse()
            if r.status != 200:
                return
            self.ok.set()
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data: "):
                    self.q.put(json.loads(line[6:]))
        except Exception as e:  # noqa: BLE001
            self.q.put({"type": "_sse_error", "error": str(e)})

    def wait(self, pred, timeout):
        end = time.time() + timeout
        seen = []
        while time.time() < end:
            try:
                ev = self.q.get(timeout=max(0.05, end - time.time()))
            except queue.Empty:
                break
            seen.append(ev)
            if pred(ev):
                return ev, seen
        return None, seen


class FakeHelper:
    """像 DeckVoice / voice_helper 一样连 8779：收 J 和 P 帧，play_mark 回 playback_done。"""

    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.s.settimeout(None)
        self.wl = threading.Lock()
        self.json = []
        self.pcm = []
        threading.Thread(target=self._read, daemon=True).start()

    def send(self, kind, payload):
        with self.wl:
            self.s.sendall(kind + struct.pack(">I", len(payload)) + payload)

    def send_json(self, obj):
        self.send(b"J", json.dumps(obj).encode())

    def _read(self):
        try:
            while True:
                head = self._exact(5)
                kind, n = head[:1], struct.unpack(">I", head[1:])[0]
                payload = self._exact(n) if n else b""
                if kind == b"J":
                    m = json.loads(payload)
                    self.json.append(m)
                    if m.get("t") == "play_mark":
                        self.send_json({"t": "playback_done", "id": m.get("id")})
                elif kind == b"P":
                    self.pcm.append((struct.unpack("<I", payload[:4])[0], (len(payload) - 4) // 4))
        except Exception:  # noqa: BLE001
            pass

    def _exact(self, n):
        b = bytearray()
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c:
                raise ConnectionError
            b += c
        return bytes(b)

    def close(self):
        try:
            self.s.shutdown(socket.SHUT_RDWR)  # 先 shutdown：另一个线程还卡在 recv 里时 close 不会立刻断开
        except OSError:
            pass
        try:
            self.s.close()
        except OSError:
            pass


def http_json(port, method, path, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def start_service(env, log_path):
    f = open(log_path, "ab")
    p = subprocess.Popen([sys.executable, os.path.join(WINDOWS, "voice_launch.py"), "--voice"], env=env,
                         stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    PROCS.append(p)
    return p


def wait_status(port, p, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if p.poll() is not None:
            return None
        try:
            code, st = http_json(port, "GET", "/status", timeout=3)
            if code == 200:
                return st
        except OSError:
            pass
        time.sleep(0.3)
    return None


def stop_service(p):
    if p.poll() is None:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()


def tail(path, n=40):
    try:
        return "\n".join(open(path, encoding="utf-8", errors="replace").read().splitlines()[-n:])
    except OSError:
        return ""


def test_service(tmp):
    import numpy as np

    root = os.path.join(tmp, "voice-root")
    os.makedirs(root)
    models_src = os.environ.get("DECK_TEST_VOICE_MODELS")
    have_models = bool(models_src and os.path.isdir(os.path.join(models_src, "kokoro-multi-lang-v1_1")))
    if have_models:
        os.symlink(models_src, os.path.join(root, "models"), target_is_directory=True) if not IS_WIN else \
            shutil.copytree(models_src, os.path.join(root, "models"))
    hp, kp = free_port(), free_port()
    json.dump({"http_port": hp, "helper_port": kp, "auto_models": False}, open(os.path.join(root, "config.json"), "w"))
    env = dict(os.environ, DECK_OS="win", DECK_VOICE_ROOT=root, DECK_VOICE_LOGS=os.path.join(tmp, "logs"),
               DECK_VOICE_NO_HELPER="1", DECK_VOICE_AUTO_MODELS="0", DECK_VOICE_LOG="-", DECK_VOICE_HOTKEYS="0",
               PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    log = os.path.join(tmp, "voice-service.log")
    p = start_service(env, log)
    st = wait_status(hp, p)
    check(st is not None, "语音服务 60 秒内应该起来（/status）")
    if st is None:
        print(tail(log), flush=True)
        stop_service(p)
        return
    print("语音服务模块：%s" % st.get("modules"), flush=True)
    for m in ("asr", "tts", "stopword", "barge"):
        check(m in (st.get("modules") or []), "语音服务应该加载 %s 模块" % m)

    # 再起一份：端口被占，应该马上正常退出，不抢端口
    p2 = start_service(env, os.path.join(tmp, "voice-second.log"))
    try:
        rc = p2.wait(40)
    except subprocess.TimeoutExpired:
        p2.kill()
        rc = None
    check(rc == 0 and "已被占用" in tail(os.path.join(tmp, "voice-second.log")), "端口被占时第二份应该直接退出 0：rc=%r" % rc)

    sse = SSE(hp)
    check(sse.ok.wait(10), "SSE /events 应该连得上")
    ev, _ = sse.wait(lambda e: e.get("type") == "status", 5)
    check(ev is not None and "audio" in ev, "SSE 一连上应该先发 status")

    h = FakeHelper(kp)
    h.send_json({"t": "hello", "platform": "win", "aec": False, "captions": False})
    h.send_json({"t": "aec", "on": False})
    h.send_json({"t": "mic", "on": True})
    ev, _ = sse.wait(lambda e: e.get("type") == "helper.hello", 5)
    check(ev is not None and ev.get("platform") == "win", "小助手的 hello 应该推到页面（helper.hello）")
    t = np.arange(16000, dtype=np.float32) / 16000
    sine = (0.2 * np.sin(2 * np.pi * 440 * t)).astype("<f4")
    for i in range(10):
        h.send(b"A", sine[i * 1600:(i + 1) * 1600].tobytes())
        time.sleep(0.02)
    time.sleep(0.6)
    code, st = http_json(hp, "GET", "/status")
    a = st.get("audio") or {}
    check(st.get("helper") is True and st.get("mic") is True, "/status 应该报小助手已连、麦克风开着：%r" % {k: st.get(k) for k in ("helper", "mic")})
    check(a.get("chunks_5s", 0) >= 10 and abs((a.get("seconds_5s") or 0) - 1.0) < 0.15, "/status 应该数到 10 块、1 秒音频：%r" % a)
    check(a.get("peak") and abs(a["peak"] - 0.2) < 0.02, "峰值应该约 0.2：%r" % a.get("peak"))

    # 快捷键「停」→ 服务广播 stop，并让小助手清播放
    h.send_json({"t": "hotkey", "name": "stop"})
    ev, _ = sse.wait(lambda e: e.get("type") == "stop", 5)
    check(ev is not None and ev.get("source") == "hotkey", "小助手发来的 stop 快捷键应该变成 stop 事件（source=hotkey）")
    time.sleep(0.3)
    check(any(m.get("t") == "stop" for m in h.json), "stop 应该发给小助手清播放")
    # 回声消除：服务进入「想」时会要 vp，小助手照实回 false；这里只检查服务认 aec 消息不出错
    h.send_json({"t": "aec", "on": False})

    # 朗读：有模型应该出声（P 帧），没模型应该干净地报 tts.error「还没下载」，服务不崩
    code, _ = http_json(hp, "POST", "/assistant", {"text": "你好，这是一次语音测试。", "done": True, "full": "你好，这是一次语音测试。"})
    check(code == 200, "/assistant 应该 200")
    if have_models:
        ev, seen = sse.wait(lambda e: e.get("type") in ("tts.first_audio", "tts.error"), 60)
        check(ev is not None and ev.get("type") == "tts.first_audio", "有模型时应该合成出声：%r" % (ev or [e.get("type") for e in seen][-10:]))
        end = time.time() + 30
        while time.time() < end and not any(m.get("t") == "play_mark" for m in h.json):
            time.sleep(0.2)
        check(bool(h.pcm) and h.pcm[0][0] > 0, "小助手应该收到 P 帧（采样率 + PCM）")
        check(any(m.get("t") == "play_mark" for m in h.json), "每块后面应该跟 play_mark")
        ev, _ = sse.wait(lambda e: e.get("type") == "state" and e.get("state") == "idle", 40)
        check(ev is not None, "念完应该回到 idle")
    else:
        ev, seen = sse.wait(lambda e: e.get("type") in ("tts.error", "tts.first_audio"), 30)
        err = (ev or {}).get("error", "")
        import importlib.util
        has_sherpa = importlib.util.find_spec("sherpa_onnx") is not None
        ok = ev is not None and ev.get("type") == "tts.error" and ("还没下载" in err or (not has_sherpa and "sherpa_onnx" in err))
        check(ok, "没模型时应该报 tts.error「还没下载」：%r" % (ev or [e.get("type") for e in seen][-10:]))
        check(not h.pcm, "没模型时不该发 P 帧")
    check(p.poll() is None, "朗读之后语音服务应该还活着")

    # 模型接口（Deck 设置 → 语音的按钮用）
    code, ms = http_json(hp, "GET", "/models/status")
    check(code == 200 and "packs" in ms and set(ms["packs"]) >= {"wake", "asr", "tts"}, "/models/status 应该列出模型包：%r" % code)
    if not have_models:
        check(set(ms.get("missing_default") or []) == {"wake", "asr", "tts"}, "空目录应该三个包都缺：%r" % ms.get("missing_default"))
    code, _ = http_json(hp, "POST", "/models/download", {"packs": ["nope"]})
    check(code == 400, "不认识的模型包应该 400")
    code, _ = http_json(hp, "GET", "/asr/last")
    check(code == 200, "/asr/last 应该能用")
    code, tv = http_json(hp, "GET", "/tts/voices")
    vids = [v["id"] for v in tv.get("voices", [])] if code == 200 else []
    check(code == 200 and tv.get("config", {}).get("voice") == "kokoro:zf_001", "Windows 上默认音色应该是 Kokoro：%r" % tv.get("config"))
    check(not any(v["installed"] for v in tv.get("voices", []) if v["engine"] != "kokoro"), "Windows 上千问音色都不该显示已装：%r" % vids)

    h.close()
    ev, _ = sse.wait(lambda e: e.get("type") == "helper" and e.get("connected") is False, 5)
    check(ev is not None, "小助手断开应该推 helper connected=false")
    check(p.poll() is None, "语音服务应该一直活着")
    stop_service(p)
    if FAIL:
        print("—— 语音服务日志（最后 40 行）——\n" + tail(log), flush=True)

    # 再起一次，这次用自带的小助手（voice_helper 线程）：应该自己连上；没声卡时麦克风报原因，不崩
    env2 = dict(env)
    env2.pop("DECK_VOICE_NO_HELPER")
    log2 = os.path.join(tmp, "voice-service-2.log")
    p = start_service(env2, log2)
    st = wait_status(hp, p)
    check(st is not None, "第二次（带小助手）语音服务应该起来")
    if st is not None:
        end = time.time() + 20
        while time.time() < end and not st.get("helper"):
            time.sleep(0.5)
            st = http_json(hp, "GET", "/status")[1]
        check(st.get("helper") is True, "自带的小助手应该连上语音服务")
        time.sleep(1.0)
        st = http_json(hp, "GET", "/status")[1]
        print("自带小助手：mic=%s reason=%r audio=%s" % (st.get("mic"), st.get("mic_reason"), st.get("audio")), flush=True)
        check(st.get("mic") is True or st.get("mic_reason") in ("no_device", "no_permission"),
              "麦克风要么开着，要么说清楚原因：%r" % {k: st.get(k) for k in ("mic", "mic_reason")})
        code, _ = http_json(hp, "POST", "/mic", {"on": False})
        time.sleep(0.8)
        st = http_json(hp, "GET", "/status")[1]
        check(code == 200 and st.get("mic") is False, "POST /mic off 后麦克风应该关")
        check(os.path.isfile(os.path.join(root, "helper-win.json")), "小助手应该记住麦克风开关（helper-win.json）")
        check(p.poll() is None, "带小助手的语音服务应该一直活着")
    else:
        print(tail(log2), flush=True)
    stop_service(p)


# ================================================================ 4. 真声卡（只在 Windows 上，有设备才测）

def test_devices():
    if not IS_WIN:
        print("非 Windows：跳过真声卡检查", flush=True)
        return
    try:
        import sounddevice as sd
    except Exception as e:  # noqa: BLE001
        check(False, "Windows 上 sounddevice 应该能导入：%s" % e)
        return
    for kind in ("input", "output"):
        try:
            dev = sd.query_devices(kind=kind)
        except Exception as e:  # noqa: BLE001  云电脑通常没有声卡
            print("没有默认%s设备，跳过：%s" % ("输入" if kind == "input" else "输出", e), flush=True)
            continue
        try:
            if kind == "input":
                frames = sd.rec(int(0.3 * 16000), samplerate=16000, channels=1, dtype="float32", blocking=True)
                check(frames.shape == (4800, 1), "16k 录 0.3 秒应该拿到 4800 个采样")
            else:
                import numpy as np
                sd.play(np.zeros(int(0.2 * 48000), np.float32), 48000, blocking=True)
            print("默认%s设备能用：%s" % ("输入" if kind == "input" else "输出", dev.get("name")), flush=True)
        except Exception as e:  # noqa: BLE001
            print("默认%s设备打不开（云电脑常见），跳过：%s" % ("输入" if kind == "input" else "输出", e), flush=True)


def main():
    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix="deck-voice-")
    try:
        for name, fn in (("模型下载", lambda: test_models(tmp)), ("播放队列", test_player),
                         ("语音服务", lambda: test_service(tmp)), ("真声卡", test_devices)):
            print("== %s" % name, flush=True)
            try:
                fn()
            except Exception:  # noqa: BLE001
                import traceback
                check(False, "%s 出错：\n%s" % (name, traceback.format_exc()))
    finally:
        for p in PROCS:
            if p.poll() is None:
                p.kill()
        shutil.rmtree(tmp, ignore_errors=True)  # 测试自己建的临时目录
    print("voice：通过 %d 项，失败 %d 项，用时 %.0f 秒（%s）" % (PASS[0], len(FAIL), time.time() - t0,
                                                   "Windows" if IS_WIN else "非 Windows，不碰真声卡"), flush=True)
    os._exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
