"""Deck 语音服务入口：
  - 127.0.0.1:8779  DeckVoice.app 连进来（帧：1 字节类型 + 4 字节大端长度 + 负载）
  - 127.0.0.1:8778  Deck 页面：SSE /events 和几个小接口
运行：manager/voice/service/run.sh（重复执行安全：端口被占就直接退出 0）
"""
import importlib
import json
import socket
import socketserver
import struct
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from . import hub
from .config import CFG

_mods = {}


# ---------- helper 通道 ----------

def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("helper 断开")
        buf += chunk
    return bytes(buf)


def _on_helper_json(msg):
    t = msg.get("t")
    if t == "mic":
        hub._set_mic(msg.get("on"), msg.get("reason", ""), msg.get("apps"))
    elif t == "partial":
        hub.emit({"type": "apple.partial", "text": msg.get("text", "")})
    elif t == "apple_final":
        hub.emit({"type": "apple.final", "text": msg.get("text", "")})
    elif t == "playback_done":
        hub.emit({"type": "playback_done", "id": msg.get("id")})
    elif t == "hotkey":
        hub.emit({"type": "hotkey", "name": msg.get("name")})
    elif t == "aec":
        hub.aec_on = bool(msg.get("on"))
        print(f"[helper] 回声消除{'开' if hub.aec_on else '关'}", flush=True)
    elif t == "quit":
        hub.helper_quit()
    elif t == "log":
        print(f"[helper] {msg.get('text')}", flush=True)
    else:
        hub.emit({"type": "helper." + str(t), **{k: v for k, v in msg.items() if k != "t"}})


_say_open = False  # /assistant 正在收一段话（还没收到 done）

class HelperHandler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        wlock = threading.Lock()

        def send(kind, payload):
            with wlock:
                sock.sendall(kind + struct.pack(">I", len(payload)) + payload)

        print("[server] DeckVoice 已连接", flush=True)
        hub._attach_helper(send)
        # 新连接时把当前需要的状态同步给 helper
        if hub.state() == "listening" and "asr" in _mods:
            _mods["asr"].sync_helper()
        try:
            while True:
                head = _recv_exact(sock, 5)
                kind, n = head[:1], struct.unpack(">I", head[1:])[0]
                if n > 16 * 1024 * 1024:
                    raise ConnectionError(f"帧太大 {n}")
                payload = _recv_exact(sock, n) if n else b""
                if kind == b"A":
                    hub.feed_audio(np.frombuffer(payload, dtype="<f4").copy())
                elif kind == b"J":
                    try:
                        _on_helper_json(json.loads(payload.decode("utf-8")))
                    except Exception:  # noqa: BLE001
                        print(f"[server] helper JSON 处理出错:\n{traceback.format_exc()}", flush=True)
        except (ConnectionError, OSError) as e:
            print(f"[server] DeckVoice 断开：{e}", flush=True)
        finally:
            hub._attach_helper(None)
            hub._set_mic(False)


class HelperServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


# ---------- HTTP ----------

def _self_origins():
    port = CFG["http_port"]
    return (f"http://127.0.0.1:{port}", f"http://localhost:{port}")


class Http(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 60  # 空闲的长连接 60 秒后断开（SSE 每 15 秒有心跳，不受影响）

    def log_message(self, fmt, *args):
        pass

    def _origin_ok(self):
        # 本服务自己的页面（8778/enroll 等）在 Safari 里 POST 会带 Origin: http://127.0.0.1:8778，
        # 同源也要放行，不然按钮点了没反应（403）。
        o = self.headers.get("Origin")
        return o is None or o in CFG["allowed_origins"] or o in _self_origins()

    def _cors(self):
        o = self.headers.get("Origin")
        if o and o in CFG["allowed_origins"]:
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Vary", "Origin")

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        # 长度不合法或超过 1 MB 一律当空包，不让一个请求卡住线程或吃光内存
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > (1 << 20):
            self.close_connection = True  # 没读走的请求体不能留给下一个请求
            return b""
        if n == 0:
            return b""
        return self.rfile.read(n)

    def _host_ok(self):
        # 挡 DNS 重绑定：别的网站把自己的域名解析到 127.0.0.1 后，Host 头会是它自己的域名
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("127.0.0.1", "localhost", "::1")

    def _json(self, raw):
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:  # noqa: BLE001
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _sse(self):
        q = hub.sse_subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self._cors()
        self.end_headers()
        self.close_connection = True
        try:
            first = {"type": "status", **hub.status(), "ts": round(time.time(), 3)}
            self.wfile.write(f"data: {json.dumps(first, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    data = q.get(timeout=15)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                except Exception:  # queue.Empty → 心跳
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            hub.sse_unsubscribe(q)

    def _dispatch(self, method):
        if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return self._send(403, {"error": "只允许本机"})
        if not self._host_ok():
            return self._send(403, {"error": "只允许本机"})
        if not self._origin_ok():
            return self._send(403, {"error": "来源不允许"})
        u = urllib.parse.urlsplit(self.path)
        path, query = u.path, dict(urllib.parse.parse_qsl(u.query))
        body = self._body() if method == "POST" else b""

        if method == "GET" and path == "/events":
            return self._sse()
        if method == "GET" and path == "/status":
            st = hub.status()
            if "asr" in _mods:
                st["asr"] = _mods["asr"].info()
            st["modules"] = sorted(_mods)
            return self._send(200, st)
        if method == "POST" and path == "/mic":
            hub.mic(self._json(body).get("on", True))
            return self._send(200, {"ok": True})
        if method == "POST" and path == "/stop":
            d = self._json(body)
            print("[page] /stop source=%s page=%s state=%s" % (str(d.get("source", "ui"))[:12], str(d.get("page", "?"))[:8], hub.state()), flush=True)
            hub.stop(d.get("source", "ui"))
            return self._send(200, {"ok": True})
        if method == "POST" and path == "/listen":
            if "asr" not in _mods:
                return self._send(503, {"error": "识别模块没加载"})
            _mods["asr"].start_listening(source=self._json(body).get("source", "ui"))
            return self._send(200, {"ok": True})
        if method == "POST" and path == "/assistant":
            d = self._json(body)
            text = d.get("text", "")
            global _say_open
            if not _say_open and (text or d.get("done")):
                # 一段新的话开始了：记下是哪个窗口发的（只记开头，不记每个增量）
                print("[page] /assistant 开始 page=%s：%s" % (str(d.get("page", "?"))[:8], str(text)[:24].replace("\n", " ")), flush=True)
            _say_open = not d.get("done")
            if text:
                hub.emit({"type": "assistant.delta", "text": text})
            if d.get("done"):
                hub.emit({"type": "assistant.done", "text": d.get("full", "")})
            return self._send(200, {"ok": True})
        if method == "POST" and path == "/say":
            tts = _mods.get("tts")
            if not tts or not hasattr(tts, "say"):
                return self._send(503, {"error": "语音合成模块没加载"})
            d = self._json(body)
            tts.say(d.get("text", ""))
            return self._send(200, {"ok": True})

        h = hub.find_route(method, path)
        if h:
            code, out, ctype = h(method, path, query, body)
            return self._send(code, out, ctype)
        tts = _mods.get("tts")
        if tts and hasattr(tts, "http"):
            r = tts.http(method, path, body)
            if r is not None:
                code, ctype, out = r
                return self._send(code, out, ctype)
        return self._send(404, {"error": "没有这个接口"})

    def do_GET(self):
        try:
            self._dispatch("GET")
        except Exception:  # noqa: BLE001
            print(traceback.format_exc(), flush=True)
            self._send(500, {"error": "服务出错"})

    def do_POST(self):
        try:
            self._dispatch("POST")
        except Exception:  # noqa: BLE001
            print(traceback.format_exc(), flush=True)
            self._send(500, {"error": "服务出错"})


# ---------- 模块 ----------

def _load_modules():
    """各线程的模块都是可选的：文件不存在就跳过，所以可以各自独立合并。"""
    plan = [
        ("asr", lambda m: m.start()),
        ("stopword", lambda m: m.start()),
        ("wake", lambda m: m.start(hub)),
        ("enroll", lambda m: m.register(hub)),
        ("assist", lambda m: m.register(hub)),  # Deck 设置 → 助手：改名、名字体检、唤醒/声纹测试
        ("tts", lambda m: m.start()),
        ("earcon", lambda m: m.start()),  # 开/关提示音
        ("barge", lambda m: m.start()),  # 开口打断：先暂停后决定
    ]
    for name, init in plan:
        try:
            mod = importlib.import_module(f"{__package__}.{name}")
        except ModuleNotFoundError as e:
            if e.name and e.name.endswith(name):
                continue
            print(f"[server] 模块 {name} 缺依赖：{e}", flush=True)
            continue
        except Exception:  # noqa: BLE001
            print(f"[server] 模块 {name} 导入失败:\n{traceback.format_exc()}", flush=True)
            continue
        try:
            init(mod)
            _mods[name] = mod
            print(f"[server] 已加载 {name}", flush=True)
        except Exception:  # noqa: BLE001
            print(f"[server] 模块 {name} 启动失败:\n{traceback.format_exc()}", flush=True)


def _port_busy(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


class _Stamped:
    """日志每行前面加时间（DeckVoice 把 stdout 接到日志文件，原来没有时间不好查）。"""

    def __init__(self, raw):
        self.raw, self.bol = raw, True

    def write(self, s):
        out = []
        for part in s.splitlines(keepends=True):
            if self.bol:
                out.append(time.strftime("%m-%d %H:%M:%S "))
            out.append(part)
            self.bol = part.endswith("\n")
        return self.raw.write("".join(out))

    def __getattr__(self, k):
        return getattr(self.raw, k)


def main():
    sys.stdout = _Stamped(sys.stdout)
    sys.stderr = _Stamped(sys.stderr)
    if _port_busy(CFG["http_port"]) or _port_busy(CFG["helper_port"]):
        print("[server] 端口已被占用，语音服务应该已经在跑了", flush=True)
        return 0
    helper = HelperServer(("127.0.0.1", CFG["helper_port"]), HelperHandler)
    threading.Thread(target=helper.serve_forever, name="helper-tcp", daemon=True).start()
    ThreadingHTTPServer.daemon_threads = True
    http = ThreadingHTTPServer(("127.0.0.1", CFG["http_port"]), Http)
    _load_modules()
    hub.emit({"type": "startup", "modules": sorted(_mods)})
    print(f"[server] 语音服务就绪 http://127.0.0.1:{CFG['http_port']}  helper:{CFG['helper_port']}  模块：{sorted(_mods)}", flush=True)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
