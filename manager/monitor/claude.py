"""Claude Code：读本机 ~/.claude/projects 下的会话日志（和 ccusage 同一口径），
统计今天 / 最近 5 小时的 token、正在进行的会话。日志里的对话正文不会被读出或发出去，只取用量字段。

订阅的 5 小时 / 每周额度（11-09 改）：主要靠 `claude -p /usage`——Claude Code 自己的 /usage 命令，
不花 token、不建会话（--no-session-persistence），登录过期它自己会续，和你在 Claude Code 里看到的一模一样。
备用：① Claude Code 状态栏给的 rate_limits（scripts/claude-statusline.py 记的，只有在终端里用 claude 时才会更新）；
② 可选（默认关）用钥匙串里的登录凭据查内部接口。三者谁新用谁，并把「这是几点的数据」一起给页面和 Vesper。
"""
import glob
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone

from .util import HOME, SourceError, find_exe, http_json, run

ACTIVE_WINDOW = 10 * 60
SCAN_DAYS = 8
# scripts/claude-statusline.py 把 Claude Code 给状态栏的 rate_limits 记在这里
RL_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs", "claude-rate-limits.json")


def _ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return None


_MON = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
# 「Current session: 19% used · resets Nov 9 at 9:30pm (Asia/Singapore)」
_USAGE_RE = re.compile(r"^\s*Current (session|week)(?:\s*\(([^)]*)\))?\s*:\s*([\d.]+)%\s*used(?:.*?\bresets\s+(.+?))?\s*$", re.I)
_RESET_RE = re.compile(r"^(?:([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,?\s*(\d{4}))?\s*(?:at\s+)?)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\s*(?:\(([^)]+)\))?$", re.I)


def _zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name) if name else None
    except Exception:  # noqa: BLE001
        return None


def _reset_ts(text, now=None):
    """「Nov 12 at 6am (Asia/Singapore)」「9:30pm (Asia/Singapore)」→ 时间戳；看不懂就返回 None。"""
    m = _RESET_RE.match((text or "").strip())
    if not m:
        return None
    mon, day, year, hh, mm, ap, tzname = m.groups()
    h, mi = int(hh), int(mm or 0)
    if ap:
        h = h % 12 + (12 if ap.lower() == "pm" else 0)
    if h > 23 or mi > 59:
        return None
    tz = _zone(tzname) or datetime.now().astimezone().tzinfo
    cur = datetime.fromtimestamp(now or time.time(), tz)
    try:
        if mon:
            mo = _MON.get(mon[:3].lower())
            if not mo:
                return None
            cands = [datetime(int(year), mo, int(day), h, mi, tzinfo=tz)] if year else \
                [datetime(cur.year + dy, mo, int(day), h, mi, tzinfo=tz) for dy in (-1, 0, 1)]
            # 没写年份：取离现在最近的那一年（12 月底看 1 月的重置时间也对）
            dt = min(cands, key=lambda d: abs((d - cur).total_seconds()))
        else:
            dt = cur.replace(hour=h, minute=mi, second=0, microsecond=0)
            if dt < cur - timedelta(minutes=5):
                dt += timedelta(days=1)
    except ValueError:
        return None
    return dt.timestamp()


def parse_usage(text, now=None):
    """解析 Claude Code /usage 的文字输出 → {five_hour, seven_day, seven_day_xxx}。"""
    res = {}
    for line in (text or "").splitlines():
        m = _USAGE_RE.match(line)
        if not m:
            continue
        kind, sub, pct, reset = m.groups()
        sub = (sub or "").strip()
        if kind.lower() == "session":
            k, label = "five_hour", "5 小时"
        elif not sub or sub.lower() in ("all models", "all"):
            k, label = "seven_day", "本周"
        else:
            k, label = "seven_day_" + re.sub(r"[^a-z0-9]+", "_", sub.lower()).strip("_"), "本周 " + sub
        if k in res:
            continue
        res[k] = {"label": label, "used_pct": round(min(100.0, float(pct)), 1),
                  "resets_at": _reset_ts(reset, now) if reset else None}
    return res if ("five_hour" in res or "seven_day" in res) else {}


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
        self.limits_via = None  # usage / statusline / api：额度是从哪读的
        self.limits_ts = None   # 额度是几点的数据
        self.usage = None       # 最近一次 `claude -p /usage` 成功的结果 {"limits", "at"}
        self.usage_try = 0
        self.usage_err = None
        self.api = None         # 联网接口最近一次成功的结果

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

    def _usage_cli(self):
        """跑一次 `claude -p /usage`：Claude Code 自己的额度命令，0 token、不建会话，约 5 秒。"""
        exe = find_exe("claude", [self.cfg["claude_path"]] if self.cfg.get("claude_path") else [])
        if not exe:
            self.usage_err = "没找到 claude 命令"
            return
        try:
            # stdin 给空：不然 claude -p 可能等着读管道；Windows 上不闪黑框。
            # --setting-sources local + 在主目录跑：不加载用户设置里的 hooks / 插件（刘海提示之类不会每 3 分钟被触发一次），
            # 登录信息不在设置里，照样能查
            r = subprocess.run([exe, "-p", "/usage", "--no-session-persistence", "--output-format", "json",
                                "--setting-sources", str(self.cfg.get("usage_setting_sources", "local"))],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=int(self.cfg.get("usage_timeout", 40)), cwd=HOME,
                               creationflags=0x08000000 if os.name == "nt" else 0)
            code, out, err = r.returncode, r.stdout or "", r.stderr or ""
        except Exception as e:  # noqa: BLE001
            code, out, err = -1, "", str(e)
        text = ""
        try:
            o = json.loads(out)
            text = o.get("result") or "" if isinstance(o, dict) else ""
        except ValueError:
            text = out or ""
        lim = parse_usage(text)
        if lim:
            self.usage, self.usage_err = {"limits": lim, "at": time.time()}, None
            return
        low = (text + " " + (err or "")).lower()
        self.usage_err = ("Claude Code 没登录或登录过期：在终端运行 claude 登录一次" if re.search(r"log ?in|auth|expired|401", low)
                          else "claude /usage 没给出额度（%s）" % ("超时" if code == -1 else "退出码 %s" % code))

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
        every = int(self.cfg.get("usage_interval", 180))
        if self.cfg.get("usage_cli", True) and (now - self.usage_try >= every or (force and now - self.usage_try >= 30)):
            self._usage_cli()
            # 失败时隔 3 倍时间再试，免得 claude 坏了每几分钟起一次进程
            self.usage_try = now + (every * 2 if self.usage_err else 0)
        local = self._local_limits()
        # 谁新用谁：/usage（几分钟一次）和状态栏（你在终端用 claude 时随时更新）
        cands = []
        if self.usage:
            cands.append((self.usage["at"], "usage", self.usage["limits"]))
        if local:
            cands.append((local["at"], "statusline", local["limits"]))
        best = max(cands, key=lambda c: c[0]) if cands else None
        if (not best or now - best[0] > 1800) and self.cfg.get("usage_limits") and \
                (force or now - self.limits_at > int(self.cfg.get("limits_interval", 300))):
            # 前两种都没有或都超过半小时：才去查会限流的联网接口
            self.limits_at = now
            self.limits = None
            self._fetch_limits()
            if self.limits:
                self.api = {"limits": self.limits, "at": now}
        if self.api and (not best or self.api["at"] > best[0]):
            best = (self.api["at"], "api", self.api["limits"])
        if best:
            self.limits, self.limits_ts, self.limits_via = best[2], best[0], best[1]
            self.limits_src = best[0] if best[1] == "statusline" else None
            if now - best[0] < 1800:
                self.limits_err = None
        else:
            self.limits, self.limits_ts, self.limits_via, self.limits_src = None, None, None, None
        # 额度读不到或已经过时（超过半小时）才报原因
        err = None
        if not best or now - best[0] > 1800:
            err = (self.limits_err if self.cfg.get("usage_limits") else None) or self.usage_err
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
            "limits": _readable(self.limits, now), "limits_enabled": bool(self.cfg.get("usage_limits") or self.cfg.get("usage_cli", True)),
            "limits_error": err, "limits_from_cc": self.limits_src,
            "limits_at": self.limits_ts, "limits_via": self.limits_via,
            # 给 Vesper 看的：这份额度是几点的
            "limits_as_of": time.strftime("%m-%d %H:%M", time.localtime(self.limits_ts)) if self.limits_ts else None,
        }


def _readable(limits, now):
    """每个窗口多带几个人话字段，Vesper 直接念、不用自己换算时间戳：剩余百分比、几点重置、还有多久。"""
    if not limits:
        return limits
    out = {}
    for k, w in limits.items():
        w = dict(w)
        w["remaining_pct"] = round(max(0.0, 100 - float(w.get("used_pct") or 0)), 1)
        r = w.get("resets_at")
        if r:
            w["resets_local"] = time.strftime("%m-%d %H:%M", time.localtime(r))
            w["resets_in"] = _span(r - now)
        out[k] = w
    return out


def _span(s):
    if s <= 60:
        return "马上"
    d, h, m = int(s // 86400), int(s % 86400 // 3600), int(s % 3600 // 60)
    return ("%d 天 %d 小时后" % (d, h)) if d else ("%d 小时 %d 分后" % (h, m)) if h else ("%d 分钟后" % max(1, m))
