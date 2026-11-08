"""每日清理卡片：读清理日志（~/Library/Logs/weekly-cache-clean.log，文件名沿用以前的每周清理）
和 daily_clean 的状态（每 3 小时检测一次能腾出多少、每晚 23:30 的自动清理结果）。
只读，不碰任何文件。清理规矩：只移进废纸篓，绝不直接删除。"""
import datetime
import glob
import json
import os
import re
import time

LOG = os.path.expanduser("~/Library/Logs/weekly-cache-clean.log")
try:  # Windows 版的清理日志在 %APPDATA%\Deck\logs 里（winport/cleaner.py）
    import deckos
    if deckos.WIN:
        LOG = deckos.support("Deck", "logs", "weekly-cache-clean.log")
except ImportError:
    pass
JSON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs", "cache-clean")


def _home(p):
    h = os.path.expanduser("~")
    return "~" + p[len(h):] if p and p.startswith(h) else p


def _iso(s):
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


def parse_json_dir(d):
    """读每次清理一份的 JSON：{ran_at, moved:[{path,trash_path,bytes}], total_bytes, skipped:[{path,reason}]}"""
    runs = []
    for fn in sorted(glob.glob(os.path.join(d, "*.json"))):
        if os.path.basename(fn) == "latest.json":
            continue
        try:
            with open(fn, encoding="utf-8") as f:
                j = json.load(f)
        except (OSError, ValueError):
            continue
        items = [{"path": _home(m.get("path", "")), "name": os.path.basename(str(m.get("path", "")).rstrip("/")),
                  "trash": _home(m.get("trash_path", "")), "mb": round((m.get("bytes") or 0) / 1048576)}
                 for m in j.get("moved") or []]
        failed = [_home(s.get("path", "")) for s in j.get("skipped") or [] if s.get("reason") == "移动失败"]
        runs.append({"at": _iso(j.get("ran_at")), "items": items, "failed": failed,
                     "total_mb": round((j.get("total_bytes") or 0) / 1048576), "done": True, "note": ""})
    runs.sort(key=lambda r: r["at"] or 0, reverse=True)
    return runs
LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.*)$")
MOVED = re.compile(r"移到废纸篓：(.+?)(?: -> (.+?))? \((\d+)M\)\s*$")  # 早期试跑格式带「 -> 废纸篓路径」
FAILED = re.compile(r"失败：(.+)$")
DONE = re.compile(r"完成：移走 (\d+) 项，共 (\d+)M")


def _ts(s):
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def parse(text):
    """把日志切成一次次清理：[{at, items:[{path,name,mb}], failed:[], total_mb, done, note}]，新的在前。"""
    runs, cur = [], None
    for raw in text.splitlines():
        m = LINE.match(raw.rstrip())
        if not m:
            continue
        at, msg = _ts(m.group(1)), m.group(2).strip()
        if msg.startswith("开始清理"):
            cur = {"at": at, "items": [], "failed": [], "total_mb": 0, "done": False, "note": ""}
            runs.append(cur)
            continue
        if cur is None:
            continue
        mv = MOVED.search(msg)
        if mv:
            path = mv.group(1)
            cur["items"].append({"path": _home(path), "name": os.path.basename(path.rstrip("/")) or path,
                                 "trash": _home(mv.group(2)) if mv.group(2) else "", "mb": int(mv.group(3))})
            continue
        fl = FAILED.search(msg)
        if fl:
            cur["failed"].append(_home(fl.group(1)))
            continue
        dn = DONE.search(msg)
        if dn:
            cur["done"] = True
            cur["total_mb"] = int(dn.group(2))
            tail = msg.split("；", 1)[1] if "；" in msg else ""
            cur["note"] = "" if tail.startswith("清单") else tail
    for r in runs:
        if not r["total_mb"]:
            r["total_mb"] = sum(i["mb"] for i in r["items"])
    runs.reverse()
    return runs


class CleanupSource:
    key = "cleanup"
    title = "每日清理"
    default_interval = 300

    def __init__(self, cfg):
        self.log = os.path.expanduser(cfg.get("log", LOG))
        self.dir = os.path.expanduser(cfg.get("dir", JSON_DIR))

    def collect(self, force=False):
        runs = []
        if os.path.exists(self.log):
            with open(self.log, encoding="utf-8", errors="replace") as f:
                runs = parse(f.read())
        if not runs and os.path.isdir(self.dir):
            runs = parse_json_dir(self.dir)
        try:
            import daily_clean
            st = daily_clean.state()
        except Exception:  # noqa: BLE001
            st = {}
        last = runs[0] if runs else None
        daily = st.get("last_daily")
        level = "ok"
        why = []
        # 没移动成功的（多半在用或没权限）只在卡片里列出来，不把状态标成「注意」：下次清理会再试，不需要他做什么
        if daily and daily.get("deepseek_down") and time.time() - daily["at"] < 2 * 86400:
            level = "warn"
            why.append({"text": "昨晚 DeepSeek 没连上，只清了缓存", "fix": "其余的留到今晚 23:30 再核对"})
        sysd = st.get("sysdata") or {}  # 系统数据的提醒放在「Mac 状态」卡（11-06 主页重排），这里只带上数据给详情用
        week = time.time() - 7 * 86400
        return {"level": level, "why": why, "scheduled": True, "next_at": st.get("next_at"), "auto": st.get("auto", True),
                "pending": st.get("pending"), "sysdata": sysd, "checked_at": st.get("checked_at"), "daily": daily,
                "last": last, "runs": runs[:12],
                "month_mb": sum(r["total_mb"] for r in runs if r["at"] and r["at"] > time.time() - 30 * 86400),
                "week_mb": sum(r["total_mb"] for r in runs if r["at"] and r["at"] > week),
                "rule": "只移进回收站，不直接删除" if LOG.endswith(os.path.join("Deck", "logs", "weekly-cache-clean.log")) else "只移进废纸篓，不直接删除"}
