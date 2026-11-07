"""Vesper 的开/关提示音：唤醒或按 ← 开始听时响「开启音」，说再见或按 → 时响「关闭音」。

声音文件放在 ~/Library/Application Support/Deck/voice/earcons/：open.wav、close.wav（16 bit PCM wav，单声道或立体声都行）。
没有文件就不响。开关和音量在同目录的 earcons.json：{"on": true, "volume": 0.6}。
通过 hub.play 交给 DeckVoice 播放（和 Vesper 说话同一路输出），换文件后发 POST /earcon/reload 或重启服务生效。
"""
import json
import threading
import time
import wave

import numpy as np

from . import hub
from .voicepaths import HOME

DIR = HOME / "earcons"
CONFIG = DIR / "earcons.json"
DEFAULTS = {"on": True, "volume": 0.6}
PLAY_SR = 24000  # 和 TTS 同采样率，DeckVoice 不用为提示音重接播放节点
OPEN_SOURCES = ("wake", "hotkey", "ui")  # 追问窗口、没听清重试这些自动续听的不响

_lock = threading.Lock()
_sounds = {}
_cfg = dict(DEFAULTS)
_last = {"open": 0.0, "close": 0.0}


def _read_wav(path):
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path.name} 不是 16 bit PCM")
        ch, sr = w.getnchannels(), w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != PLAY_SR and len(x) > 1:
        n = int(round(len(x) * PLAY_SR / sr))
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    x = x[: int(PLAY_SR * 1.5)]  # 提示音最多 1.5 秒
    fade = min(len(x), int(PLAY_SR * 0.01))  # 两头 10ms 淡入淡出，防爆音
    if fade:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)
        x[:fade] *= ramp
        x[-fade:] *= ramp[::-1]
    return x


def reload():
    global _cfg
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text("utf-8")))
    except Exception:  # noqa: BLE001
        pass
    sounds = {}
    for name in ("open", "close"):
        p = DIR / f"{name}.wav"
        if p.exists():
            try:
                sounds[name] = _read_wav(p)
            except Exception as e:  # noqa: BLE001
                print(f"[earcon] 读不了 {p}：{e}", flush=True)
    with _lock:
        _cfg = cfg
        _sounds.clear()
        _sounds.update(sounds)
    print(f"[earcon] 提示音：{sorted(sounds) or '无'}，{'开' if cfg['on'] else '关'}，音量 {cfg['volume']}", flush=True)
    return info()


def info():
    with _lock:
        return {"on": bool(_cfg.get("on")), "volume": _cfg.get("volume"), "sounds": sorted(_sounds), "dir": str(DIR)}


def play(name):
    with _lock:
        x = _sounds.get(name)
        on, vol = _cfg.get("on", True), float(_cfg.get("volume", 0.6))
    now = time.monotonic()
    if x is None or not on or now - _last[name] < 0.8:  # 连按/连发只响一次
        return False
    _last[name] = now
    return hub.play(np.clip(x * max(0.0, min(vol, 1.0)), -1, 1), PLAY_SR)


def _on_listen(ev):
    if ev.get("on") and ev.get("source") in OPEN_SOURCES:
        play("open")


def _on_dismiss(ev):
    play("close")


def _on_followup_end(ev):
    # 接话窗口（1 分钟）到了：轻轻响一下关闭音，之后要重新叫名字
    _play_soft("close", 0.5)


def _play_soft(name, k):
    with _lock:
        x = _sounds.get(name)
        on, vol = _cfg.get("on", True), float(_cfg.get("volume", 0.6))
    now = time.monotonic()
    if x is None or not on or now - _last[name] < 0.8:
        return False
    _last[name] = now
    return hub.play(np.clip(x * max(0.0, min(vol * k, 1.0)), -1, 1), PLAY_SR)


def _http(method, path, query, body):
    if method == "POST" and path.endswith("/reload"):
        return 200, json.dumps(reload(), ensure_ascii=False).encode(), "application/json; charset=utf-8"
    if method == "POST" and path.rstrip("/") == "/earcon":
        try:
            req = json.loads(body or b"{}")
        except ValueError:
            return 400, b'{"error":"bad json"}', "application/json"
        DIR.mkdir(parents=True, exist_ok=True)
        cfg = dict(DEFAULTS)
        try:
            cfg.update(json.loads(CONFIG.read_text("utf-8")))
        except Exception:  # noqa: BLE001
            pass
        if "on" in req:
            cfg["on"] = bool(req["on"])
        if "volume" in req:
            cfg["volume"] = max(0.0, min(float(req["volume"]), 1.0))
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
        return 200, json.dumps(reload(), ensure_ascii=False).encode(), "application/json; charset=utf-8"
    if method == "POST" and path.endswith("/test"):
        name = "close" if "close" in (query or {}) or b"close" in (body or b"") else "open"
        _last[name] = 0.0
        return 200, json.dumps({"played": play(name)}).encode(), "application/json"
    return 200, json.dumps(info(), ensure_ascii=False).encode(), "application/json; charset=utf-8"


def start():
    reload()
    hub.on("listen", _on_listen)
    hub.on("dismiss", _on_dismiss)
    hub.on("followup.end", _on_followup_end)
    hub.route("GET", "/earcon", _http)
    hub.route("POST", "/earcon", _http)
