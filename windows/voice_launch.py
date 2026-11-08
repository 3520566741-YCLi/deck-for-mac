"""Deck for Windows：语音服务怎么跟着 Deck 一起起来（11-08 第三步）。

Mac 上是 Deck.app 打开 DeckVoice.app，DeckVoice 再跑 service/run.sh 并守护它。Windows 上：

  Deck.exe（窗口 + 托盘 + 8777 后台）
    └─ voice_launch.start()：守护线程，起一个子进程并在它退出后重启（退避 2/5/15/60 秒，一小时最多 10 次）
         └─ Deck.exe --voice（开发时是 python windows/voice_launch.py --voice）
              ├─ 语音服务 manager/voice/service（127.0.0.1:8778 给页面，8779 给小助手）
              ├─ 小助手线程 voice_helper.py（麦克风、播放、Ctrl+Alt+M / Ctrl+Alt+J 快捷键），通过 8779 连服务
              └─ 第一次启动时自动下载语音模型（voice_models.py，约 375MB），页面能看到 models.progress 事件

为什么单独一个进程：模型加载后要几百 MB 内存，onnxruntime / 声卡驱动出问题崩了也不会把 Deck 窗口带走；
打包后的 exe 没法 python -m，所以用同一个 Deck.exe 加 --voice 参数。
8778 已经有服务在跑（比如开了两个 Deck、或手动起过一份）就不再起第二份。
Deck 退出后子进程两秒内自己退出（盯着父进程）。日志：%LOCALAPPDATA%\\Deck\\logs\\voice.log

语音服务上多挂的接口（Deck 设置 → 语音里的按钮用）：
  GET  http://127.0.0.1:8778/models/status     装了哪些、缺哪些、正在下载的进度
  POST http://127.0.0.1:8778/models/download   {"packs": ["wake","asr","tts"]}（不写 = 这三个），后台下载
  POST http://127.0.0.1:8778/models/cancel     停下（已下的部分留着，下次续传）

环境变量（测试用）：DECK_VOICE_NO_HELPER=1 不起小助手；DECK_VOICE_AUTO_MODELS=0 不自动下载；
DECK_VOICE_LOG=- 日志打到控制台；DECK_VOICE_HOTKEYS=0 不注册快捷键。
"""
import collections
import json
import os
import socket
import subprocess
import sys
import threading
import time

APP = "Deck"
RESTART_NOW = 75   # 子进程用这个退出码表示「请马上重启我」（下完唤醒模型后要重新加载）
HERE = os.path.dirname(os.path.abspath(__file__))


def bundle_dir():
    """manager/ 在哪：打包后在 exe 旁边的 _internal/manager，开发时在仓库里（和 deck_win.bundle_dir 一样）。"""
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "manager")
    return os.path.join(os.path.dirname(HERE), "manager")


def log_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    d = os.path.join(base, APP, "logs")
    os.makedirs(d, exist_ok=True)
    return d


def _paths():
    """让 `import service.xxx`（manager/voice/service）和 deckos、winport（manager/）都能导入。
    manager/ 放在最后，免得它里面的同名文件挡住别的模块。"""
    mdir = bundle_dir()
    vdir = os.path.join(mdir, "voice")
    if vdir not in sys.path:
        sys.path.insert(0, vdir)
    if mdir not in sys.path:
        sys.path.append(mdir)
    return mdir, vdir


def _port_busy(port):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", int(port))) == 0


def _ports():
    """8778 / 8779（用户可在 <语音目录>/config.json 改）。"""
    _paths()
    from service.config import CFG  # noqa: PLC0415
    return int(CFG["http_port"]), int(CFG["helper_port"])


# ================================================================ 子进程：Deck.exe --voice

def _redirect_logs():
    """打包后的 Deck.exe 是窗口程序，没有控制台；开发时被父进程拉起也没有。输出写进 voice.log（超过 5MB 换新）。"""
    if os.environ.get("DECK_VOICE_LOG") == "-" or (sys.stdout is not None and sys.stdout.isatty()):
        return None
    path = os.path.join(log_dir(), "voice.log")
    try:
        if os.path.getsize(path) > 5 * 1024 * 1024:
            os.replace(path, path + ".1")
    except OSError:
        pass
    f = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115  进程活多久它开多久
    sys.stdout = sys.stderr = f
    return f


def _utf8_console():
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _alive(pid):
    try:
        import psutil  # noqa: PLC0415
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)  # POSIX 上 0 号信号只是问一下在不在（Windows 上 os.kill 会直接结束进程，所以上面单独处理）
        return True
    except OSError:
        return False


def _watch_parent(pid):
    def loop():
        while True:
            time.sleep(2)
            if not _alive(pid):
                print("[voice-launch] Deck 已退出，语音服务跟着退出", flush=True)
                os._exit(0)
    threading.Thread(target=loop, name="watch-parent", daemon=True).start()


class _Models:
    """语音服务里的模型下载：/models/* 接口 + 第一次启动自动下载 + 进度事件。"""

    def __init__(self, hub, server, root, supervised):
        import voice_models  # noqa: PLC0415  windows/voice_models.py
        self.vm = voice_models
        self.hub = hub
        self.server = server
        self.root = root
        self.supervised = supervised
        self.stop_ev = threading.Event()
        self.last = None
        self.result = None
        self._t = 0.0
        hub.route("*", "/models/", self.http)

    def _progress(self, d):
        self.last = d
        now = time.monotonic()
        if now - self._t >= 0.5 or d.get("phase") in ("done", "retry"):
            self._t = now
            self.hub.emit({"type": "models.progress", **d})

    def start(self, packs, why="ui"):
        if self.vm.busy():
            return False
        self.stop_ev.clear()
        self.result = None

        def run():
            print("[voice-launch] 开始下载语音模型 %s（%s）" % (",".join(packs), why), flush=True)
            r = self.vm.download(packs, self.root, self._progress, self.stop_ev)
            self.result = r
            print("[voice-launch] 模型下载结束：%s" % json.dumps(r, ensure_ascii=False), flush=True)
            self.hub.emit({"type": "models.done", **r})
            self._maybe_restart(r)

        threading.Thread(target=run, name="voice-models", daemon=True).start()
        return True

    def _maybe_restart(self, r):
        """唤醒 / 「停」/ 声纹模型是服务启动时加载的：新装了它们就重启一下语音服务（识别和朗读用到时才加载，不用重启）。"""
        new = set(r.get("installed") or [])
        wake = getattr(self.server, "_mods", {}).get("wake")
        if not (new & {"kws", "speaker", "silero_vad"}) or getattr(wake, "engine", None) is not None:
            return   # 唤醒已经在跑（wake.start 没模型时也算「已加载」，但 engine 是 None）
        if not self.supervised:
            self.hub.emit({"type": "models.restart_needed"})
            return
        for _ in range(600):  # 等她不在听 / 想 / 说的时候
            if self.hub.state() == "idle":
                break
            time.sleep(1)
        print("[voice-launch] 唤醒模型装好了，重启语音服务以加载", flush=True)
        os._exit(RESTART_NOW)

    def auto(self):
        try:
            st = self.vm.status(self.root)
        except Exception as e:  # noqa: BLE001
            print("[voice-launch] 看模型状态出错：%s" % e, flush=True)
            return
        miss = st["missing_default"]
        if not miss:
            return
        from service.config import CFG  # noqa: PLC0415
        if os.environ.get("DECK_VOICE_AUTO_MODELS") == "0" or not CFG.get("auto_models", True):
            print("[voice-launch] 缺语音模型 %s，没开自动下载" % miss, flush=True)
            self.hub.emit({"type": "models.missing", "packs": miss})
            return
        self.start(miss, "auto")

    def http(self, method, path, query, body):
        if path == "/models/status" and method == "GET":
            out = self.vm.status(self.root)
            out.update(busy=self.vm.busy(), progress=self.last, result=self.result)
            return 200, json.dumps(out, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"
        if path == "/models/download" and method == "POST":
            try:
                d = json.loads(body.decode("utf-8")) if body else {}
            except ValueError:
                d = {}
            packs = d.get("packs") or list(self.vm.DEFAULT_PACKS)
            if not isinstance(packs, list) or not all(isinstance(p, str) and p in self.vm.PACKS for p in packs):
                return 400, json.dumps({"error": "不认识的模型包"}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"
            if not self.start(packs):
                return 409, json.dumps({"error": "已经在下载了"}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"
            return 202, b'{"ok":true,"started":true}', "application/json"
        if path == "/models/cancel" and method == "POST":
            self.stop_ev.set()
            return 200, b'{"ok":true}', "application/json"
        return 404, json.dumps({"error": "没有这个接口"}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"


def run_service(argv=None):
    """Deck.exe --voice 的入口：语音服务 + 小助手。端口被占（已有一份在跑）就直接返回 0。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    logf = _redirect_logs()
    _utf8_console()
    try:
        import faulthandler  # noqa: PLC0415  onnxruntime / 声卡驱动这类原生崩溃也留个堆栈
        faulthandler.enable(file=logf or sys.stderr)
    except Exception:  # noqa: BLE001
        pass
    parent = None
    if "--parent-pid" in argv:
        try:
            parent = int(argv[argv.index("--parent-pid") + 1])
        except (IndexError, ValueError):
            parent = None
    if parent:
        _watch_parent(parent)
    _paths()
    from service.config import CFG, ROOT  # noqa: PLC0415
    http_port, helper_port = int(CFG["http_port"]), int(CFG["helper_port"])
    if _port_busy(http_port) or _port_busy(helper_port):
        print("[voice-launch] 端口 %d/%d 已被占用，语音服务应该已经在跑了" % (http_port, helper_port), flush=True)
        return 0
    os.makedirs(ROOT, exist_ok=True)
    from service import hub, server  # noqa: PLC0415
    models = None
    try:
        models = _Models(hub, server, ROOT, supervised=bool(parent))
    except Exception as e:  # noqa: BLE001  下载模块坏了不影响语音服务本身
        print("[voice-launch] 模型下载接口没挂上：%s" % e, flush=True)
    if os.environ.get("DECK_VOICE_NO_HELPER") != "1":
        import voice_helper  # noqa: PLC0415  windows/voice_helper.py
        voice_helper.start_thread(helper_port, ROOT, hotkeys=os.environ.get("DECK_VOICE_HOTKEYS") != "0")
    if models is not None:
        threading.Timer(3.0, models.auto).start()
    return server.main() or 0


# ================================================================ 父进程：Deck.exe 里守护子进程

class Supervisor:
    BACKOFF = (2, 5, 15, 60)
    MAX_PER_HOUR = 10

    def __init__(self):
        self.proc = None
        self.fails = 0
        self.next = 0.0
        self.tries = collections.deque()
        self.stopping = False
        self.http_port = 8778

    def cmd(self):
        me = ["--voice", "--parent-pid", str(os.getpid())]
        if getattr(sys, "frozen", False):
            return [sys.executable] + me
        return [sys.executable, os.path.abspath(__file__)] + me

    def spawn(self):
        flags = 0
        if sys.platform == "win32":
            flags = 0x08000000 | 0x00004000   # CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS（别跟窗口抢 CPU）
        env = dict(os.environ)
        env.pop("DECK_VOICE_LOG", None)
        self.proc = subprocess.Popen(self.cmd(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True, env=env)
        self.tries.append(time.monotonic())
        print("[voice-launch] 语音服务已启动（pid %d）" % self.proc.pid, flush=True)

    def loop(self):
        try:
            self.http_port, _ = _ports()
        except Exception as e:  # noqa: BLE001
            print("[voice-launch] 读语音配置出错，用默认端口：%s" % e, flush=True)
        while not self.stopping:
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001  守护线程不能死
                print("[voice-launch] 守护出错：%s" % e, flush=True)
            time.sleep(2)

    def tick(self):
        now = time.monotonic()
        if self.proc is not None:
            rc = self.proc.poll()
            if rc is None:
                if now - self.tries[-1] > 120:
                    self.fails = 0   # 稳定跑了两分钟，退避清零
                return
            self.proc = None
            if rc == RESTART_NOW:
                print("[voice-launch] 语音服务要求重启", flush=True)
                self.next = now
            elif rc != 0:
                self.fails += 1
                wait = self.BACKOFF[min(self.fails - 1, len(self.BACKOFF) - 1)]
                self.next = now + wait
                print("[voice-launch] 语音服务退出（%s），%d 秒后重启" % (rc, wait), flush=True)
            else:
                self.next = now + 10   # 正常退出：多半是端口已被别的一份占了
        if now < self.next or self.stopping:
            return
        if _port_busy(self.http_port):
            return                     # 已有一份在跑（别处起的）：不起第二份，接着看
        while self.tries and now - self.tries[0] > 3600:
            self.tries.popleft()
        if len(self.tries) >= self.MAX_PER_HOUR:
            return
        self.spawn()

    def stop(self):
        self.stopping = True
        p = self.proc
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass


_sup = None


def start():
    """Deck 窗口进程里调一次：后台守护语音服务子进程。重复调用无害。"""
    global _sup
    if _sup is not None:
        return _sup
    _sup = Supervisor()
    threading.Thread(target=_sup.loop, name="voice-supervisor", daemon=True).start()
    return _sup


def stop():
    if _sup is not None:
        _sup.stop()


if __name__ == "__main__":
    if "--voice" in sys.argv:
        sys.exit(run_service())
    print(__doc__)
