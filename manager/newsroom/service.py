"""资讯页的后台：按时段编报纸、管预算、管长桥行情小助手。

数据都在 ~/Library/Application Support/Deck/news/：
  editions/YYYYMMDD-HHMM.json   每期一份（只留 30 天）
  usage.json                     每天花了多少（估算）
  market.json                    长桥小助手写的行情快照
  lb-venv/                       长桥 SDK 的虚拟环境（第一次点「连接长桥」时装）
"""
import glob
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta

from . import editor, sources
from .editor import BJ

SUPPORT = os.path.expanduser("~/Library/Application Support/Deck/news")
HERE = os.path.dirname(os.path.abspath(__file__))
LB_VERSION = "5.3.0"  # 固定版本；升级前先在这台 Mac 上试
WATCH_IDLE = 600       # 10 分钟没人看投资页，就停掉行情小助手


def _now():
    return datetime.now(BJ)


class NewsRoom:
    def __init__(self, cfg, deepseek_key, log=None):
        self.cfg = dict(cfg or {})
        self.key = deepseek_key
        self.log = log or (lambda m: print(m, flush=True))
        self.slots = self.cfg.get("slots") or ["06:30", "12:30", "20:00"]
        self.budget = float(self.cfg.get("daily_budget_cny", 1.0))
        self.lock = threading.Lock()
        self.busy = False
        self.last_error = ""
        self.last_report = []
        self.proc = None
        self.proc_lock = threading.Lock()
        self.market_seen = 0.0
        self.setup_state = ""
        self.invest_seen = set()
        self.invest_last = 0.0
        self.usage_lock = threading.Lock()
        self.fail_at = 0.0
        self.fails = 0
        for sub in ("editions",):
            os.makedirs(os.path.join(SUPPORT, sub), exist_ok=True)

    # ---------------- 存取 ----------------
    def _editions(self):
        return sorted(glob.glob(os.path.join(SUPPORT, "editions", "*.json")))

    def latest(self):
        files = self._editions()
        if not files:
            return None
        try:
            with open(files[-1], encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _usage(self):
        try:
            with open(os.path.join(SUPPORT, "usage.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def spent_today(self):
        return float(self._usage().get(_now().strftime("%Y-%m-%d"), {}).get("cny", 0.0))

    def _add_usage(self, cny, what):
        with self.usage_lock:
            self._add_usage_locked(cny, what)

    def _add_usage_locked(self, cny, what):
        u = self._usage()
        day = _now().strftime("%Y-%m-%d")
        d = u.setdefault(day, {"cny": 0.0, "calls": 0})
        d["cny"] = round(d["cny"] + float(cny or 0), 4)
        d["calls"] += 1
        d["last"] = what
        for k in sorted(u)[:-40]:  # 只留 40 天
            u.pop(k, None)
        _write_json(os.path.join(SUPPORT, "usage.json"), u)

    def _prune(self):
        cut = (_now() - timedelta(days=30)).strftime("%Y%m%d")
        for p in self._editions():
            if os.path.basename(p)[:8] < cut:
                try:
                    os.remove(p)  # 只删我们自己写的旧报纸文件
                except OSError:
                    pass

    # ---------------- 时段 ----------------
    def current_slot(self, now=None):
        """现在应该有的那一期（今天已经过去的最后一个时段；早于第一个时段就是昨天最后一期）。"""
        now = now or _now()
        today = [now.replace(hour=int(s[:2]), minute=int(s[3:5]), second=0, microsecond=0) for s in self.slots]
        past = [t for t in today if t <= now]
        if past:
            return past[-1]
        y = now - timedelta(days=1)
        s = self.slots[-1]
        return y.replace(hour=int(s[:2]), minute=int(s[3:5]), second=0, microsecond=0)

    def next_slot(self, now=None):
        now = now or _now()
        for s in self.slots:
            t = now.replace(hour=int(s[:2]), minute=int(s[3:5]), second=0, microsecond=0)
            if t > now:
                return t
        s = self.slots[0]
        return (now + timedelta(days=1)).replace(hour=int(s[:2]), minute=int(s[3:5]), second=0, microsecond=0)

    def slot_name(self, t):
        h = t.hour
        return "晨版" if h < 11 else ("午版" if h < 17 else "晚版")

    def due(self):
        """该编新一期了吗：当前时段还没有报纸、不在高峰、预算没用完。"""
        if not self.cfg.get("enabled", True) or not self.key():
            return False
        slot = self.current_slot()
        ed = self.latest()
        if ed and ed.get("slot_ts", 0) >= int(slot.timestamp()):
            return False
        if editor.is_peak():
            return False
        # 失败后别每分钟重试（每次都花钱）：30 分钟、1 小时、2 小时……最长 4 小时再试
        if self.fails and time.time() - self.fail_at < min(4 * 3600, 1800 * (2 ** (self.fails - 1))):
            return False
        return self.spent_today() < self.budget

    # ---------------- 编报纸 ----------------
    def generate(self, manual=False):
        with self.lock:
            if self.busy:
                return {"error": "正在编这一期，稍等"}
            self.busy = True
        try:
            if self.spent_today() >= self.budget:
                raise editor.EditorError("今天的资讯预算（¥%.2f）用完了，停在上一期" % self.budget)
            key = self.key()
            if not key:
                raise editor.EditorError("找不到 DeepSeek key")
            items, report = sources.gather(self.cfg, self.log)
            self.last_report = report
            stock = self.stock_items()
            body, meta = editor.edit(items + stock, key, self.cfg, self.log)
            slot = self.current_slot()
            now = _now()
            ed = {"v": 1, "made_at": int(time.time()), "slot_ts": int(slot.timestamp()),
                  "slot": slot.strftime("%H:%M"), "name": self.slot_name(slot), "date": now.strftime("%Y-%m-%d"),
                  "issue": self._issue_no(), "manual": manual, "sources": report,
                  "n_read": len(items) + len(stock), "meta": meta, **body}
            fn = os.path.join(SUPPORT, "editions", now.strftime("%Y%m%d-%H%M") + ".json")
            _write_json(fn, ed)
            self._add_usage(meta["cost"], "编报纸")
            self.invest_seen.update(i["id"] for i in stock)
            self.last_error = ""
            self.fails = 0
            self._prune()
            self.log("[news] %s 编好了：读 %d 条，花 ¥%.3f，%ss" % (ed["name"], ed["n_read"], meta["cost"], meta["seconds"]))
            return {"ok": True}
        except editor.EditorError as e:
            if e.usage:
                self._add_usage(editor.cost_cny(e.usage, self.cfg.get("price"), editor.is_peak()), "编报纸（失败）")
            self.fails += 1
            self.fail_at = time.time()
            self.last_error = str(e)
            self.log("[news] 这一期没编成：%s" % e)
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.fails += 1
            self.fail_at = time.time()
            self.last_error = "编报纸出错：%s" % e
            return {"error": self.last_error}
        finally:
            self.busy = False

    def _issue_no(self):
        return len(self._editions()) + 1 + int(self.cfg.get("issue_offset", 0))

    def start(self):
        threading.Thread(target=self._loop, name="newsroom", daemon=True).start()

    def _loop(self):
        time.sleep(20)  # 让管家服务先起来
        while True:
            try:
                if self.due():
                    self.generate()
                self._invest_tick()
                self._watch_helper()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(60)

    # ---------------- 投资：自选股资讯 ----------------
    def stock_items(self):
        m = self.market()
        out = []
        for n in m.get("news") or []:
            it = sources.item("长桥资讯", "stock", n.get("title"), n.get("url") or "https://longbridge.com", n.get("summary"),
                              n.get("ts") if isinstance(n.get("ts"), (int, float)) else 0, symbols=[n.get("symbol")])
            if it:
                out.append(it)
        return out

    def _invest_tick(self):
        """有新的自选股资讯时，非高峰时段最多每 30 分钟让 DeepSeek 标一次影响（小调用，几厘钱）。"""
        if time.time() - self.invest_last < 1800 or editor.is_peak() or self.busy:
            return
        if self.spent_today() >= self.budget or not self.key():
            return
        new = [i for i in self.stock_items() if i["id"] not in self.invest_seen]
        if not new:
            return
        self.invest_last = time.time()
        try:
            tagged, meta = editor.tag_invest(new, self.key(), self.cfg)
        except editor.EditorError as e:
            if e.usage:
                self._add_usage(editor.cost_cny(e.usage, self.cfg.get("price"), editor.is_peak()), "自选资讯（失败）")
            self.invest_seen.update(i["id"] for i in new)  # 失败的这批不再重试，免得反复花钱
            self.log("[news] 自选资讯打标失败：%s" % e)
            return
        self.invest_seen.update(i["id"] for i in new)
        if meta:
            self._add_usage(meta["cost"], "自选资讯")
        files = self._editions()
        if not files or not tagged:
            return
        ed = self.latest() or {}
        old = [x for x in ed.get("invest") or [] if x["id"] not in {t["id"] for t in tagged}]
        ed["invest"] = (tagged + old)[:8]
        ed["invest_at"] = int(time.time())
        _write_json(files[-1], ed)

    # ---------------- 长桥行情小助手 ----------------
    def venv_python(self):
        return os.path.join(SUPPORT, "lb-venv", "bin", "python")

    def market(self):
        try:
            with open(os.path.join(SUPPORT, "market.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def market_status(self):
        """给页面的：行情快照 + 连接状态。顺便记一下「有人在看」，小助手按需启动。"""
        self.market_seen = time.time()
        if not os.path.exists(self.venv_python()):
            state = "setup" if self.setup_state == "installing" else "not_installed"
            return {"state": state, "error": self.setup_state if self.setup_state not in ("", "installing") else ""}
        self._watch_helper()
        m = self.market()
        if m and time.time() - (m.get("updated") or 0) > 30 and m.get("state") == "ok":
            m["stale"] = True
        m.pop("auth_url", None)  # 授权链接只在本机浏览器里开，不往页面上放
        return m or {"state": "starting"}

    def _watch_helper(self):
        with self.proc_lock:
            alive = self.proc is not None and self.proc.poll() is None
            watched = time.time() - self.market_seen < WATCH_IDLE
            if alive and not watched:
                self.proc.terminate()
                self.proc = None
                return
            if alive or not watched or not os.path.exists(self.venv_python()):
                return
            if self.proc is not None and self.proc.returncode == 3:
                return  # 还没授权：等他点「连接长桥」
            if self.proc is not None and time.time() - getattr(self.proc, "started", 0) < 60:
                return  # 刚挂过，别一直重启
            self._spawn(False)

    def _spawn(self, login):
        kill_stale_helper()  # 长桥一个账号只能有一条长连接：上一次管家服务留下的小助手先停掉
        log = os.path.join(SUPPORT, "longbridge.log")
        try:
            if os.path.getsize(log) > 2 * 1024 * 1024:
                os.replace(log, log + ".1")
        except OSError:
            pass
        with open(log, "a", encoding="utf-8") as logf:
            p = subprocess.Popen([self.venv_python(), os.path.join(HERE, "lb_helper.py"), "--dir", SUPPORT] + (["--login"] if login else []),
                                 stdout=logf, stderr=logf, stdin=subprocess.DEVNULL, cwd=SUPPORT,
                                 env=dict(os.environ, LONGBRIDGE_ENABLE_OVERNIGHT="true"))
        p.started = time.time()
        p.login = login
        self.proc = p
        return p

    def connect(self):
        """他在 Deck 里点了「连接长桥」：没装 SDK 就先装（后台），装好后弹浏览器授权。"""
        if self.setup_state == "installing":
            return {"ok": True, "state": "setup"}
        threading.Thread(target=self._connect, daemon=True).start()
        return {"ok": True, "state": "setup" if not os.path.exists(self.venv_python()) else "authorizing"}

    def _connect(self):
        if not os.path.exists(self.venv_python()):
            self.setup_state = "installing"
            ok, msg = install_sdk()
            self.setup_state = "" if ok else msg
            if not ok:
                return
        with self.proc_lock:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.terminate()
            self.market_seen = time.time()
            self._spawn(True)

    # ---------------- 给页面 ----------------
    def snapshot(self):
        ed = self.latest()
        nxt = self.next_slot()
        return {"edition": ed, "busy": self.busy, "error": self.last_error,
                "spent_today": round(self.spent_today(), 3), "budget": self.budget, "peak": editor.is_peak(),
                "next": {"name": self.slot_name(nxt), "at": nxt.strftime("%H:%M")},
                "has_key": bool(self.key()), "slots": self.slots,
                "title": self.cfg.get("title") or editor.DEFAULT_TITLE, "title_en": self.cfg.get("title_en") or editor.DEFAULT_TITLE_EN}


def kill_stale_helper():
    """按小助手自己写的 pid 文件找；确认那个进程真的是 lb_helper.py 才停（不会误伤别的进程）。"""
    pf = os.path.join(SUPPORT, "lb_helper.pid")
    try:
        with open(pf, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return
    if "lb_helper.py" in out:
        try:
            os.kill(pid, 15)
            time.sleep(0.5)
        except OSError:
            pass


def pick_python():
    """SDK 的轮子对新版 Python 支持最好：优先 Homebrew 的 3.11/3.12，没有就用系统 python3。"""
    for p in ("/opt/homebrew/bin/python3.12", "/opt/homebrew/bin/python3.11", "/usr/local/bin/python3.11", sys.executable, "/usr/bin/python3"):
        if p and os.path.exists(p):
            return p
    return "python3"


def install_sdk():
    venv = os.path.join(SUPPORT, "lb-venv")
    log = os.path.join(SUPPORT, "longbridge.log")
    with open(log, "a", encoding="utf-8") as f:
        try:
            subprocess.run([pick_python(), "-m", "venv", venv], check=True, stdout=f, stderr=f, timeout=120)
            subprocess.run([os.path.join(venv, "bin", "python"), "-m", "pip", "install", "--disable-pip-version-check",
                            "--only-binary=:all:", "longbridge==%s" % LB_VERSION], check=True, stdout=f, stderr=f, timeout=600)
            return True, ""
        except Exception as e:  # noqa: BLE001
            f.write("安装失败：%s\n" % e)
            return False, "装长桥 SDK 失败，详情在 %s" % log


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)
