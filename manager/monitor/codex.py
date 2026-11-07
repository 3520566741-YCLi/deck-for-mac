"""Codex：临时启动本机 `codex app-server --stdio`，问一次“任务列表 + 用量上限”就退出。
与原 Fusion Workbench 同一读法（也是 CodexBar 的读法）。不读登录凭据，不联网以外的地方。"""
import json
import os
import select
import subprocess
import time

from .util import HOME, SourceError, find_exe

CANDIDATES = (
    "/Applications/ChatGPT.app/Contents/Resources/codex",
    "/Applications/Codex.app/Contents/Resources/codex",
    os.path.join(HOME, ".local/bin/codex"),
)


def _window(v):
    if not isinstance(v, dict) or v.get("usedPercent") is None:
        return None
    return {"used_pct": float(v["usedPercent"]), "resets_at": v.get("resetsAt"),
            "minutes": v.get("windowDurationMins")}


class CodexSource:
    key = "codex"
    title = "Codex"
    default_interval = 60

    def __init__(self, cfg):
        self.cfg = cfg or {}

    def _exe(self):
        extra = [self.cfg["path"]] if self.cfg.get("path") else []
        for p in extra + list(CANDIDATES):
            p = os.path.expanduser(p)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
        return find_exe("codex")

    def collect(self, force=False):
        exe = self._exe()
        if not exe:
            raise SourceError("没找到 Codex 命令行。装 ChatGPT 桌面版或 `npm i -g @openai/codex` 后再试。")
        results = {}
        # 原版用 --stdio；新版本默认就是 stdio、可能不认这个参数，所以失败时去掉再试一次
        for args in (["app-server", "--stdio"], ["app-server"]):
            results = self._ask(exe, args)
            if 1 in results:
                break
        return self._parse(results)

    def _ask(self, exe, args):
        try:
            p = subprocess.Popen([exe] + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL)
        except OSError as e:
            raise SourceError("Codex 启动失败：%s" % e)
        results = {}
        buf = b""
        deadline = time.time() + float(self.cfg.get("timeout", 15))

        def send(obj):
            p.stdin.write((json.dumps(obj) + "\n").encode())
            p.stdin.flush()

        try:
            send({"id": 1, "method": "initialize",
                  "params": {"clientInfo": {"name": "deck-monitor", "version": "1.0"}}})
            sent = False
            while time.time() < deadline and not (2 in results and 3 in results):
                r, _, _ = select.select([p.stdout], [], [], 0.3)
                if r:
                    chunk = os.read(p.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            o = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(o, dict) and isinstance(o.get("id"), int) and ("result" in o or "error" in o):
                            results[o["id"]] = o.get("result")
                if 1 in results and not sent:
                    sent = True
                    send({"method": "initialized", "params": {}})
                    send({"id": 2, "method": "thread/list", "params": {"archived": False, "limit": 30}})
                    send({"id": 3, "method": "account/rateLimits/read", "params": None})
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                p.stdin.close()
            except OSError:
                pass
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        return results

    def _parse(self, results):
        if 1 not in results:
            raise SourceError("Codex 没有回应（可能需要先打开 ChatGPT / Codex 登录一次）")
        limits = results.get(3) or {}
        rl = limits.get("rateLimits")
        if not rl and isinstance(limits.get("rateLimitsByLimitId"), dict):
            by = limits["rateLimitsByLimitId"]
            rl = by.get("codex") or next((v for v in by.values() if isinstance(v, dict)), None)
        rl = rl or {}
        tasks = []
        for t in ((results.get(2) or {}).get("data") or []):
            st = t.get("status") or {}
            if st.get("type") != "active":
                continue
            flags = set(st.get("activeFlags") or [])
            tasks.append({"title": (t.get("name") or "").strip() or "未命名任务",
                          "updated_at": t.get("updatedAt"),
                          "needs_you": bool(flags & {"waitingOnApproval", "waitingOnUserInput"})})
        five, week = _window(rl.get("primary")), _window(rl.get("secondary"))
        used = max([w["used_pct"] for w in (five, week) if w] or [0])
        level = "error" if used >= 95 else "warn" if used >= 80 or any(t["needs_you"] for t in tasks) else "ok"
        why = []
        for nm, w in (("5 小时", five), ("本周", week)):
            if w and w["used_pct"] >= 80:
                why.append({"text": "Codex %s额度已用 %d%%" % (nm, w["used_pct"]), "fix": "快用完了，重要的活留到重置后再跑"})
        for t in tasks:
            if t["needs_you"]:
                why.append({"text": "Codex 任务「%s」在等你批准或回复" % t["title"][:30], "fix": "打开 Codex 看一下这个任务"})
        return {"level": level, "why": why, "five_hours": five, "week": week, "plan": rl.get("planType"),
                "tasks": tasks[:8], "limits_missing": not (five or week)}
