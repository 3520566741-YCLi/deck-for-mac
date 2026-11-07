"""语音层的中枢：事件总线 + 音频分发 + 和 DeckVoice.app 的通道。

各线程的模块只通过这里交互（见 manager/voice/INTERFACE.md）：
    from . import hub
    hub.subscribe_audio(cb)     # cb(np.ndarray float32, 16 kHz)，每个订阅者独立线程，慢了不拖累别人
    hub.on(type, cb)            # cb(event: dict)；type="*" 收全部
    hub.emit({...})             # 也会推到页面 SSE
    hub.set_state(s)            # idle / listening / thinking / speaking
    hub.play(pcm, sr, id=None)  # 非阻塞入队，由 helper 播放（同一音频引擎，回声消除才干净）
    hub.stop(source)            # 立刻停：清播放 + 发 stop
    hub.mic(on)                 # 开关麦克风
    hub.route(method, prefix, handler)  # 挂 HTTP：handler(method, path, query, body) -> (status, bytes, ctype)
    hub.recent_audio(seconds)   # 最近 N 秒麦克风音频（环形缓冲）
"""
import json
import queue
import struct
import threading
import time
import traceback

import numpy as np

SAMPLE_RATE = 16000
_lock = threading.RLock()
_listeners = {}            # type -> [cb]
_sse = []                  # [queue.Queue]
_audio_subs = []           # [(queue, thread)]
_routes = []               # [(method, prefix, handler)]
_helper_send = None        # server 设置：fn(kind: bytes, payload: bytes)
_state = "idle"
_mic_on = False
aec_on = False          # helper 报告的回声消除状态；关着时 ASR/「停」要防自己的声音
last_speaking_end = 0.0  # 助手最后一次说完（离开 speaking）的时间
_helper_connected = False
_ring = np.zeros(SAMPLE_RATE * 10, dtype=np.float32)
_ring_pos = 0
_level_t = 0.0
_events = queue.Queue()


# ---------- 事件 ----------

def on(etype, cb):
    with _lock:
        _listeners.setdefault(etype, []).append(cb)


def emit(event):
    event.setdefault("ts", round(time.time(), 3))
    data = json.dumps(event, ensure_ascii=False)
    with _lock:
        sse = list(_sse)
    for q in sse:
        try:
            q.put_nowait(data)
        except queue.Full:
            pass
    _events.put(event)


def _dispatch_loop():
    while True:
        ev = _events.get()
        with _lock:
            cbs = list(_listeners.get(ev.get("type"), [])) + list(_listeners.get("*", []))
        for cb in cbs:
            try:
                cb(ev)
            except Exception:  # noqa: BLE001
                print(f"[hub] 事件处理出错 {ev.get('type')}:\n{traceback.format_exc()}", flush=True)


threading.Thread(target=_dispatch_loop, name="hub-events", daemon=True).start()


def sse_subscribe():
    q = queue.Queue(maxsize=500)
    with _lock:
        _sse.append(q)
    return q


def sse_unsubscribe(q):
    with _lock:
        if q in _sse:
            _sse.remove(q)


# ---------- 状态 ----------

def state():
    return _state


def set_state(s):
    global _state
    with _lock:
        prev = _state
        if s == prev:
            return
        _state = s
    global last_speaking_end
    if prev == "speaking":
        last_speaking_end = time.monotonic()
    emit({"type": "state", "state": s, "prev": prev})


def status():
    return {"state": _state, "mic": _mic_on, "mic_reason": _mic_reason, "helper": _helper_connected,
            "audio": audio_stats(), "helper_relaunches_1h": len(_hl["tries"]), "helper_quit": _hl["quit"]}


# ---------- 麦克风有没有真的在送声音（11-06：换设备后 helper 显示 mic=true，其实一帧都没送过来，唤醒全哑） ----------
import collections  # noqa: E402

_astats = collections.deque()  # (时间, 采样数, 平方和, 峰值)，只留最近 10 秒
_alast = {"t": 0.0, "mic_since": time.monotonic(), "kicks": 0, "kick_at": 0.0}


def _note_audio(samples):
    now = time.monotonic()
    n = len(samples)
    if n:
        a = np.abs(samples)
        _astats.append((now, n, float(np.dot(samples, samples)), float(a.max())))
    _alast["t"] = now
    while _astats and now - _astats[0][0] > 10:
        _astats.popleft()


def audio_stats(window=5.0):
    now = time.monotonic()
    rows = [r for r in list(_astats) if now - r[0] <= window]
    n = sum(r[1] for r in rows)
    ss = sum(r[2] for r in rows)
    peak = max((r[3] for r in rows), default=0.0)
    rms = (ss / n) ** 0.5 if n else 0.0
    return {"chunks_5s": len(rows), "seconds_5s": round(n / SAMPLE_RATE, 2),
            "rms_dbfs": round(float(20 * np.log10(rms)), 1) if rms > 0 else None, "peak": round(peak, 4),
            "last_ms_ago": int((now - _alast["t"]) * 1000) if _alast["t"] else None,
            "restarts": _alast["kicks"]}


def _audio_watchdog():
    """麦克风开着却 4 秒没收到声音（或收到的全是 0）：让 helper 关一下再开麦克风，重建音频引擎。
    退避 10 / 30 / 60 秒，避免反复折腾麦克风（11-05 每秒重建引擎弄坏过 Claude 听写）。"""
    while True:
        time.sleep(2)
        try:
            now = time.monotonic()
            if not (_helper_connected and _mic_on) or now - _alast["mic_since"] < 6:
                continue
            last = _alast["t"]
            dead = (not last or now - last > 4) and now - _alast["mic_since"] > 6
            st = audio_stats(8.0)
            silent = not dead and st["seconds_5s"] > 6 and st["peak"] < 1e-6
            if not (dead or silent):
                if _alast["kicks"] and now - _alast["kick_at"] > 120:
                    _alast["kicks"] = 0
                continue
            wait = (10, 30, 60)[min(_alast["kicks"], 2)]
            if now - _alast["kick_at"] < wait:
                continue
            _alast["kicks"] += 1
            _alast["kick_at"] = now
            why = "4 秒没收到声音" if dead else "收到的全是静音"
            print(f"[hub] 麦克风开着但{why}，让 DeckVoice 重开麦克风（第 {_alast['kicks']} 次）", flush=True)
            emit({"type": "audio.stall", "why": "dead" if dead else "silent", "n": _alast["kicks"]})
            send_helper({"t": "mic", "on": False})
            time.sleep(0.8)
            send_helper({"t": "mic", "on": True})
            _alast["mic_since"] = time.monotonic()
        except Exception:  # noqa: BLE001
            print(f"[hub] 音频看门狗出错:\n{traceback.format_exc()}", flush=True)


threading.Thread(target=_audio_watchdog, name="audio-watchdog", daemon=True).start()


# ---------- DeckVoice 崩溃后自动拉起（11-07 03:56 DeckVoice 崩溃后一直没人重开，整晚叫不应） ----------
# 正常退出 DeckVoice 时它会把语音服务一起关掉，所以服务还活着却连不上 helper，基本就是 DeckVoice 崩了。
_hl = {"down_since": time.monotonic(), "tries": [], "next": 0.0, "quit": False}


def helper_quit():
    """DeckVoice 从菜单正常退出：在它下次自己连上之前不自动拉起。"""
    _hl["quit"] = True
    print("[hub] DeckVoice 正常退出，不自动重开", flush=True)
HELPER_RELAUNCH_AFTER = 15      # 断开多少秒才去拉
HELPER_RELAUNCH_BACKOFF = (15, 60, 300)
HELPER_RELAUNCH_MAX_PER_HOUR = 5


def _helper_relauncher():
    import subprocess
    import sys
    if sys.platform != "darwin":
        return
    while True:
        time.sleep(3)
        try:
            now = time.monotonic()
            if _helper_connected:
                _hl["down_since"] = None
                _hl["quit"] = False
                continue
            if _hl["quit"]:
                continue
            if _hl["down_since"] is None:
                _hl["down_since"] = now
            if now - _hl["down_since"] < HELPER_RELAUNCH_AFTER or now < _hl["next"]:
                continue
            _hl["tries"] = [t for t in _hl["tries"] if now - t < 3600]
            if len(_hl["tries"]) >= HELPER_RELAUNCH_MAX_PER_HOUR:
                continue
            _hl["tries"].append(now)
            n = len(_hl["tries"])
            _hl["next"] = now + HELPER_RELAUNCH_BACKOFF[min(n - 1, len(HELPER_RELAUNCH_BACKOFF) - 1)]
            print(f"[hub] DeckVoice 断开 {int(now - _hl['down_since'])} 秒，自动重新打开（这一小时第 {n} 次）", flush=True)
            emit({"type": "helper.relaunch", "n": n})
            subprocess.run(["/usr/bin/open", "-g", "-b", "io.github.deckformac.deckvoice"], timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            print(f"[hub] 自动拉起 DeckVoice 出错:\n{traceback.format_exc()}", flush=True)


threading.Thread(target=_helper_relauncher, name="helper-relauncher", daemon=True).start()


# ---------- 音频 ----------

def subscribe_audio(cb, name=None):
    q = queue.Queue(maxsize=200)  # 约 20 秒，慢订阅者丢最旧的

    def loop():
        while True:
            chunk = q.get()
            try:
                cb(chunk)
            except Exception:  # noqa: BLE001
                print(f"[hub] 音频订阅 {name or cb} 出错:\n{traceback.format_exc()}", flush=True)

    t = threading.Thread(target=loop, name=f"audio-{name or getattr(cb, '__name__', 'sub')}", daemon=True)
    t.start()
    with _lock:
        _audio_subs.append(q)
    return q


def feed_audio(samples):
    """server 收到 helper 的 A 帧时调用。"""
    global _ring_pos, _level_t
    n = len(samples)
    _note_audio(samples)
    with _lock:
        if n >= len(_ring):
            _ring[:] = samples[-len(_ring):]
            _ring_pos = 0
        else:
            end = _ring_pos + n
            if end <= len(_ring):
                _ring[_ring_pos:end] = samples
            else:
                k = len(_ring) - _ring_pos
                _ring[_ring_pos:] = samples[:k]
                _ring[:n - k] = samples[k:]
            _ring_pos = end % len(_ring)
        subs = list(_audio_subs)
    for q in subs:
        try:
            q.put_nowait(samples)
        except queue.Full:
            try:
                q.get_nowait()
                q.put_nowait(samples)
            except queue.Empty:
                pass
    now = time.monotonic()
    if _state == "listening" and now - _level_t >= 1 / 15:
        _level_t = now
        rms = float(np.sqrt(np.mean(samples * samples))) if n else 0.0
        emit({"type": "level", "rms": round(min(1.0, rms * 6), 3), "src": "mic"})


def recent_audio(seconds):
    n = min(int(seconds * SAMPLE_RATE), len(_ring))
    with _lock:
        r = np.concatenate([_ring[_ring_pos:], _ring[:_ring_pos]])
    return r[-n:].copy() if n > 0 else np.zeros(0, dtype=np.float32)


# ---------- helper 通道 ----------

def _attach_helper(send_fn):
    global _helper_send, _helper_connected
    _helper_send = send_fn
    _helper_connected = send_fn is not None
    _alast["mic_since"] = time.monotonic()
    emit({"type": "helper", "connected": _helper_connected})


def send_helper(obj):
    fn = _helper_send
    if fn is None:
        return False
    try:
        fn(b"J", json.dumps(obj, ensure_ascii=False).encode("utf-8"))
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[hub] 发给 helper 失败：{e}", flush=True)
        return False


def play(pcm, sr, id=None):  # noqa: A002
    """TTS 用：非阻塞把一段音频交给 helper 播放。pcm 为单声道 float32。id 不为空时，播到这里 helper 回 playback_done。"""
    fn = _helper_send
    if fn is None:
        return False
    data = np.asarray(pcm, dtype="<f4").tobytes()
    try:
        fn(b"P", struct.pack("<I", int(sr)) + data)
        if id is not None:
            fn(b"J", json.dumps({"t": "play_mark", "id": id}).encode("utf-8"))
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[hub] 播放失败：{e}", flush=True)
        return False


_last_stop = 0.0


def stop(source="voice"):
    # 已经停在 idle、半秒内又来一次 stop（页面回发、按键连按）→ 不再广播，避免事件风暴
    global _last_stop
    now = time.monotonic()
    if _state == "idle" and now - _last_stop < 0.5:
        return
    _last_stop = now
    send_helper({"t": "stop"})
    emit({"type": "stop", "source": source})
    set_state("idle")


def mic(on):
    send_helper({"t": "mic", "on": bool(on)})


_mic_reason = ""


def _set_mic(on, reason="", apps=None):
    """reason：other_app（别的 App 在录音，自动让出）/ no_permission / 空（正常开关）"""
    global _mic_on, _mic_reason
    if bool(on) and not _mic_on:
        _alast["mic_since"] = time.monotonic()
    _mic_on, _mic_reason = bool(on), reason or ""
    ev = {"type": "mic", "on": _mic_on}
    if reason:
        ev["reason"] = reason
        ev["apps"] = apps or []
    emit(ev)


# ---------- HTTP 挂载 ----------

def route(method, prefix, handler):
    with _lock:
        _routes.append((method.upper(), prefix, handler))


def find_route(method, path):
    with _lock:
        rs = list(_routes)
    best = None
    for m, p, h in rs:
        if (m == method or m == "*") and path.startswith(p):
            if best is None or len(p) > len(best[0]):
                best = (p, h)
    return best[1] if best else None
