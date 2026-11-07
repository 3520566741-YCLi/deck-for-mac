"""Mac 管家 · 监控台。

Hub 按各数据源自己的间隔在后台采集，页面只读缓存好的快照。
没人看监控台（超过 IDLE_AFTER 秒没有请求）时自动停采，不在后台白跑。"""
import threading
import time
import traceback

from .butler import ButlerSource
from .claude import ClaudeSource
from .cleanup import CleanupSource
from .codex import CodexSource
from .deepseek import DeepSeekSource
from .github import GitHubSource
from .makerworld import MakerWorldSource
from .system import SystemSource
from .tools import ToolsSource
from .util import SourceError

IDLE_AFTER = 120
MIN_FORCE_GAP = 8
DEFAULT_ORDER = ["system", "claude", "codex", "github", "deepseek", "butler", "cleanup", "tools", "makerworld"]
DEFAULT_ENABLED = {"system", "claude", "codex", "github", "deepseek", "butler", "cleanup", "tools"}


class Hub:
    def __init__(self, cfg, butler_collect=None, deepseek_key=None):
        cfg = cfg or {}
        scfg = cfg.get("sources", {})
        makers = {
            "system": lambda c: SystemSource(c),
            "claude": lambda c: ClaudeSource(c),
            "codex": lambda c: CodexSource(c),
            "github": lambda c: GitHubSource(c),
            "deepseek": lambda c: DeepSeekSource(c, deepseek_key),
            "butler": lambda c: ButlerSource(c, butler_collect),
            "cleanup": lambda c: CleanupSource(c),
            "makerworld": lambda c: MakerWorldSource(c),
            "tools": lambda c: ToolsSource(c),
        }
        self.sources = {}
        self.state = {}
        for key in DEFAULT_ORDER:
            c = scfg.get(key, {})
            enabled = c.get("enabled", key in DEFAULT_ENABLED or (key == "makerworld" and bool(c.get("user_id"))))
            if not enabled or (key == "butler" and not butler_collect):
                continue
            src = makers[key](c)
            src.interval = max(1, int(c.get("interval", src.default_interval)))
            self.sources[key] = src
            self.state[key] = {"key": key, "title": src.title, "interval": src.interval, "status": "loading",
                               "data": None, "error": None, "updated_at": None, "busy": False}
        self.order = [k for k in cfg.get("order", DEFAULT_ORDER) if k in self.sources] + \
            [k for k in self.sources if k not in cfg.get("order", DEFAULT_ORDER)]
        self.due = {k: 0 for k in self.sources}
        self.forced = {k: 0 for k in self.sources}
        self.lock = threading.Lock()
        self.seen = 0
        self.wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    # ---------- 后台循环 ----------
    def _loop(self):
        while True:
            self.wake.wait(1)
            self.wake.clear()
            if time.time() - self.seen > IDLE_AFTER:
                continue
            now = time.time()
            for key, src in self.sources.items():
                with self.lock:
                    st = self.state[key]
                    if st["busy"] or now < self.due[key]:
                        continue
                    st["busy"] = True
                    force = self.due[key] == -1
                threading.Thread(target=self._run, args=(key, src, force), daemon=True).start()

    def _run(self, key, src, force):
        t0 = time.time()
        try:
            data = src.collect(force=force)
            err = None
        except SourceError as e:
            data, err = None, str(e)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            data, err = None, "采集出错：%s" % e
        with self.lock:
            st = self.state[key]
            st["busy"] = False
            st["took"] = round(time.time() - t0, 2)
            if err:
                st["status"] = "error"
                st["error"] = err
                # 出错时保留上一次的好数据，页面会标“数据可能过期”
            else:
                st["status"] = "ok"
                st["error"] = None
                st["data"] = data
                st["updated_at"] = time.time()
            self.due[key] = time.time() + (src.interval if not err else max(src.interval, 30))

    # ---------- 给 HTTP 用 ----------
    def snapshot(self):
        self.seen = time.time()
        self.wake.set()
        with self.lock:
            return {"now": time.time(), "order": self.order,
                    "sources": {k: dict(v) for k, v in self.state.items()}}

    def refresh(self, key=None):
        """手动刷新。同一个源 8 秒内只认一次，防止连点。"""
        self.seen = time.time()
        keys = [key] if key else list(self.sources)
        accepted = []
        now = time.time()
        with self.lock:
            for k in keys:
                if k not in self.sources or now - self.forced[k] < MIN_FORCE_GAP:
                    continue
                self.forced[k] = now
                self.due[k] = -1
                accepted.append(k)
        self.wake.set()
        return accepted
