"""Claude Code：读本机 ~/.claude/projects 下的会话日志（和 ccusage 同一口径），
统计今天 / 最近 5 小时的 token、正在进行的会话。日志里的对话正文不会被读出或发出去，只取用量字段。

可选（默认关）：用 Claude Code 自己存在钥匙串里的登录凭据，查订阅的 5 小时 / 每周用量上限。
这是 Claude Code 内部用的接口，不是公开 API，随时可能变；第一次查询时 macOS 会弹窗问是否允许访问钥匙串。
"""
import glob
import json
import os
import time
from datetime import datetime, timezone

from .util import HOME, SourceError, http_json, run

ACTIVE_WINDOW = 10 * 60
SCAN_DAYS = 8
# scripts/claude-statusline.py 把 Claude Code 给状态栏的 rate_limits 记在这里
RL_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs", "claude-rate-limits.json")


def _ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return None


def _short_model(m):
    if not m or m.startswith("<"):
        return None
    m = m.replace("claude-", "")
    parts = [p for p in m.split("-") if not (p.isdigit() and len(p) == 8)]
    return "-".join(parts)


class ClaudeSource:
    key = "claude"
    title = "Claude Code"
    default_interval = 10  # 增量读日志，很省（11-06：尽量实时）

    def __init__(self, cfg):
        self.cfg = cfg or {}
        base = os.environ.get("CLAUDE_CONFIG_DIR") or self.cfg.get("config_dir") or os.path.join(HOME, ".claude")
        self.root = os.path.join(os.path.expanduser(base), "projects")
        self.files = {}   # path -> {"off", "ino", "session"}
        self.events = {}  # dedupe key -> (ts, model, in, out, cache_w, cache_r)
        self.sessions = {}  # sessionId -> info
        self.limits = None
        self.limits_at = 0
        self.limits_err = None
        self.limits_src = None  # 有值 = 额度来自 Claude Code 状态栏（那一刻的时间）

    # ---------- 增量读日志 ----------
    def _scan(self):
        cutoff = time.time() - SCAN_DAYS * 86400
        paths = glob.glob(os.path.join(self.root, "*", "*.jsonl")) + \
            glob.glob(os.path.join(self.root, "*", "*", "subagents", "*.jsonl"))
        for p in paths:
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            rec = self.files.get(p)
            if rec is None or rec["ino"] != st.st_ino or st.st_size < rec["off"]:
                rec = self.files[p] = {"off": 0, "ino": st.st_ino}
            if st.st_size == rec["off"]:
                continue
            try:
                with open(p, "rb") as f:
                    f.seek(rec["off"])
                    chunk = f.read(st.st_size - rec["off"])
            except OSError:
                continue
            end = chunk.rfind(b"\n")
            if end < 0:
                continue
            rec["off"] += end + 1
            sub = "/subagents/" in p
            for line in chunk[:end].split(b"\n"):
                self._line(line, sub, st.st_mtime)
        old = time.time() - SCAN_DAYS * 86400
        self.events = {k: v for k, v in self.events.items() if v[0] >= old}

    def _line(self, line, sub, mtime):
        if b'"usage"' not in line and b'"cwd"' not in line:
            return
        try:
            o = json.loads(line)
        except ValueError:
            return
        sid = o.get("sessionId")
        ts = _ts(o.get("timestamp") or "")
        if sid and not sub and o.get("cwd"):
            s = self.sessions.setdefault(sid, {"id": sid, "first": ts})
            s["project"] = os.path.basename(o["cwd"].rstrip("/")) or o["cwd"]
            s["branch"] = o.get("gitBranch") or s.get("branch")
            if ts and ts >= (s.get("last") or 0):
                s["last"] = ts
            if o.get("type") == "user" and isinstance(o.get("message", {}).get("content"), str):
                s["turns"] = s.get("turns", 0) + 1
        msg = o.get("message") or {}
        u = msg.get("usage") if isinstance(msg, dict) else None
        if o.get("type") != "assistant" or not u or not ts:
            return
        if sid and not sub and sid in self.sessions:
            self.sessions[sid]["model"] = _short_model(msg.get("model")) or self.sessions[sid].get("model")
        # 流式输出会把同一条回复写多行：按 message.id + requestId 去重（与 ccusage 一致）
        key = "%s:%s" % (msg.get("id"), o.get("requestId")) if msg.get("id") else "%s:%s" % (sid, o.get("uuid"))
        self.events[key] = (ts, _short_model(msg.get("model")), int(u.get("input_tokens") or 0),
                            int(u.get("output_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0),
                            int(u.get("cache_read_input_tokens") or 0))

    # ---------- 用量上限 ----------
    def _local_limits(self):
        """优先用 Claude Code 自己给状态栏的 rate_limits（scripts/claude-statusline.py 记下来的），不联网、不会限流。
        窗口已经过了重置时间的，按「已重置、用了 0%」算；整份数据超过 7 天就不用。"""
        try:
            with open(RL_FILE, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:  # noqa: BLE001
            return None
        now = time.time()
        if now - float(d.get("at") or 0) > 7 * 86400:
            return None
        res = {}
        for k, label in (("five_hour", "5 小时"), ("seven_day", "本周")):
            w = (d.get("rate_limits") or {}).get(k)
            if not isinstance(w, dict) or w.get("used_percentage") is None:
                continue
            r = _ts(w["resets_at"]) if isinstance(w.get("resets_at"), str) else w.get("resets_at")
            used = float(w["used_percentage"])
            if r and r < now:
                used, r = 0.0, None  # 已重置，下次重置时间要等 Claude Code 再报
            res[k] = {"label": label, "used_pct": round(used, 1), "resets_at": r}
        return {"limits": res, "at": float(d.get("at") or 0)} if res else None

    def _fetch_limits(self):
        code, out, err = run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"], timeout=20)
        if code != 0 or not out.strip():
            self.limits_err = "钥匙串里没找到 Claude Code 登录凭据（先在终端运行一次 claude 登录，或在弹窗里点“允许”）"
            return
        try:
            tok = json.loads(out)["claudeAiOauth"]["accessToken"]
        except Exception:  # noqa: BLE001
            self.limits_err = "Claude Code 凭据格式变了，暂时读不了用量上限"
            return
        status, body = http_json("https://api.anthropic.com/api/oauth/usage", {
            "Authorization": "Bearer " + tok, "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json"})
        tok = None
        if status != 200 or not isinstance(body, dict):
            if status == 429:
                # 被限流：退避 10、20、40 分钟，最多 1 小时（之前的额度数字保留着），不是登录问题
                self.n429 = getattr(self, "n429", 0) + 1
                wait = min(3600, 600 * 2 ** (self.n429 - 1))
                self.limits_at = time.time() + wait - int(self.cfg.get("limits_interval", 300))
                self.limits_err = ("用量上限接口请求太频繁（429），%d 分钟后自动再查" % (wait // 60)) if not self.limits else None
            elif status in (401, 403):
                self.limits_err = "Claude Code 登录过期（%s）：在终端运行 claude 重新登录" % status
            else:
                self.limits_err = "用量上限接口暂时不可用（%s），稍后自动再查" % (status or "网络错误")
            return
        res = {}
        for k, label in (("five_hour", "5 小时"), ("seven_day", "本周"), ("seven_day_opus", "本周 Opus"),
                         ("seven_day_sonnet", "本周 Sonnet")):
            w = body.get(k)
            if isinstance(w, dict) and w.get("utilization") is not None:
                res[k] = {"label": label, "used_pct": round(float(w["utilization"]), 1),
                          "resets_at": _ts(w.get("resets_at") or "")}
        self.limits = res
        self.limits_err = None
        self.n429 = 0

    def collect(self, force=False):
        if not os.path.isdir(self.root):
            raise SourceError("没找到 Claude Code 的日志目录（%s）。装好 Claude Code 并用过一次后就会出现。"
                              % self.root.replace(HOME, "~"))
        self._scan()
        now = time.time()
        local = self._local_limits()
        if local and now - local["at"] < 1800:
            # Claude Code 半小时内报过：直接用，不去查联网接口
            self.limits, self.limits_err, self.limits_src = local["limits"], None, local["at"]
        elif self.cfg.get("usage_limits") and (force or now - self.limits_at > int(self.cfg.get("limits_interval", 300))):
            self.limits_at = now
            self._fetch_limits()
            self.limits_src = None
            if not self.limits and local:  # 接口查不到时，退回 Claude Code 上次报的
                self.limits, self.limits_src, self.limits_err = local["limits"], local["at"], None
        elif local and (not self.limits or self.limits_src):
            self.limits, self.limits_src, self.limits_err = local["limits"], local["at"], None
        day0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        week0 = day0 - datetime.now().weekday() * 86400

        def agg(since):
            t = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "calls": 0, "models": {}}
            for ts, model, i, o, cw, cr in self.events.values():
                if ts < since:
                    continue
                t["input"] += i
                t["output"] += o
                t["cache_write"] += cw
                t["cache_read"] += cr
                t["calls"] += 1
                if model:
                    t["models"][model] = t["models"].get(model, 0) + i + o + cw + cr
            t["total"] = t["input"] + t["output"] + t["cache_write"] + t["cache_read"]
            return t

        # 每小时柱状：最近 24 小时
        hours = [0] * 24
        h0 = now - 24 * 3600
        for ts, _m, i, o, cw, cr in self.events.values():
            if ts >= h0:
                hours[min(23, int((ts - h0) // 3600))] += i + o + cw + cr
        active = sorted([s for s in self.sessions.values() if s.get("last") and now - s["last"] <= ACTIVE_WINDOW],
                        key=lambda s: -s["last"])
        recent = sorted([s for s in self.sessions.values() if s.get("last")], key=lambda s: -s["last"])[:6]
        code, out, _ = run(["pgrep", "-x", "claude"], timeout=3)
        procs = len(out.split()) if code == 0 else 0
        level = "ok"
        if self.limits:
            top = max(w["used_pct"] for w in self.limits.values())
            level = "error" if top >= 95 else "warn" if top >= 80 else "ok"
        why = [{"text": "Claude %s额度已用 %d%%" % (w["label"], w["used_pct"]), "fix": "快用完了，留着给重要的活"}
               for w in (self.limits or {}).values() if w["used_pct"] >= 80]
        return {
            "level": level, "why": why,
            "today": agg(day0), "five_hours": agg(now - 5 * 3600), "week": agg(week0),
            "hours": hours,
            "active": active[:6], "recent": recent, "processes": procs,
            "limits": self.limits, "limits_enabled": bool(self.cfg.get("usage_limits")),
            "limits_error": self.limits_err, "limits_from_cc": self.limits_src,
        }
