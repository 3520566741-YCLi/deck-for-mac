"""Deck 按键音效送到 iPad（11-07）。

OpenDisplay 只传画面不传声音，所以在 iPad 上装了一个后台小 App「Deck 音箱」（源码 manager/ipad-speaker/），
它在 iPad 本机 8779 端口等指令。Mac 这边不走 Wi-Fi，而是通过 iPad 已经插着的那根线：
macOS 自带的 usbmuxd（/var/run/usbmuxd）能把 Mac 上的一个连接直接接到 iPad 的某个端口，延迟一两毫秒。

协议（一行一条，UTF-8）：
    "<名字> <增益>\\n"   名字只能是 SOUNDS 里的 11 个；增益 0~1.2
    "ping\\n"           保活，2 秒一次；对面断了就重连
没有插 iPad、iPad 上没开「Deck 音箱」时 send() 返回 False，网页就自己在 Mac 上放。
"""
import os
import plistlib
import socket
import struct
import threading
import time

USBMUXD = "/var/run/usbmuxd"
PORT = 8779
SOUNDS = ("tap", "primary", "tab", "on", "off", "open", "close", "page", "done", "notify", "warn")

_lock = threading.Lock()
_sock = None
_next_try = 0.0
_pinger = None


def _mux_request(s, msg, tag):
    payload = plistlib.dumps(msg)
    s.sendall(struct.pack("<IIII", 16 + len(payload), 1, 8, tag) + payload)
    head = _recv_exact(s, 16)
    length = struct.unpack("<IIII", head)[0]
    return plistlib.loads(_recv_exact(s, length - 16))


def _recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise OSError("usbmuxd 断开")
        buf += chunk
    return buf


def _base():
    return {"ClientVersionString": "Deck", "ProgName": "Deck"}


def _usb_devices():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1.5)
    try:
        s.connect(USBMUXD)
        r = _mux_request(s, dict(_base(), MessageType="ListDevices"), 1)
    finally:
        s.close()
    out = []
    for d in r.get("DeviceList", []):
        props = d.get("Properties", {})
        if props.get("ConnectionType") == "USB":
            out.append(d.get("DeviceID"))
    return out


def _connect():
    """经 usbmuxd 连到 iPad 上的 8779。成功返回已接通的 socket，失败返回 None。"""
    if not os.path.exists(USBMUXD):
        return None
    for dev in _usb_devices():
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.5)
        try:
            s.connect(USBMUXD)
            r = _mux_request(s, dict(_base(), MessageType="Connect", DeviceID=dev,
                                     PortNumber=socket.htons(PORT)), 2)
            if r.get("MessageType") == "Result" and r.get("Number") == 0:
                s.settimeout(1.0)
                return s
        except (OSError, plistlib.InvalidFileException, struct.error, ValueError):
            pass
        s.close()
    return None


def _drop():
    global _sock
    if _sock is not None:
        try:
            _sock.close()
        except OSError:
            pass
    _sock = None


def _ensure():
    """拿到连接；断了就重连，但失败后 3 秒内不再试，免得每次按键都去问 usbmuxd。"""
    global _sock, _next_try
    if _sock is not None:
        return True
    now = time.monotonic()
    if now < _next_try:
        return False
    try:
        _sock = _connect()
    except OSError:
        _sock = None
    if _sock is None:
        _next_try = now + 3
        return False
    _start_pinger()
    return True


def _write(line):
    try:
        _sock.sendall(line.encode("utf-8"))
        return True
    except OSError:
        _drop()
        return False


def _start_pinger():
    global _pinger
    if _pinger is not None and _pinger.is_alive():
        return

    def loop():
        while True:
            time.sleep(2)
            with _lock:
                if _sock is None:
                    return
                _write("ping\n")

    _pinger = threading.Thread(target=loop, name="ipad-sfx-ping", daemon=True)
    _pinger.start()


def send(name, gain):
    """让 iPad 放一个按键音。放出去了返回 True；没连上 iPad 返回 False（网页会自己在 Mac 上放）。"""
    if name not in SOUNDS:
        return False
    try:
        g = max(0.0, min(1.2, float(gain)))
    except (TypeError, ValueError):
        g = 0.75
    with _lock:
        if not _ensure():
            return False
        if _write("%s %.2f\n" % (name, g)):
            return True
        # 刚断：马上重连一次再试
        global _next_try
        _next_try = 0
        return _ensure() and _write("%s %.2f\n" % (name, g))


def connected():
    with _lock:
        return _sock is not None
