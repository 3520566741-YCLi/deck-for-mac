"""从 Codex / Claude Code 自己写在本机的会话日志里看「谁在干活、干到哪了」（：开关打开前就在跑的任务也要能看到）。

- 只读，不改任何文件；只看最近 20 分钟内有改动的会话；每个文件只读开头 8KB（取项目目录）和末尾 256KB（取最近几步）。
- 文件没变（大小和修改时间都一样）就用上次的结果，所以 3 秒一刷也几乎不花力气。
- 没有百分比：Agent 没自己报就不编。例外是 Agent 列了计划清单（Codex 的 update_plan、Claude Code 的 TodoWrite），
  这时按「做完几项 / 一共几项」算一个，标明是按清单算的。
- 和 report_progress 自报的同一个会话（同一个 Agent、同一个项目目录、时间重叠）只显示自报那条。
"""
import glob
import hashlib
import json
import os
import re
import time

HOME = os.path.expanduser("~")
CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")
CLAUDE_HOME = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")

FRESH = 20 * 60            # 这么久没写日志的会话不看
QUIET = 5 * 60             # 正在干活却这么久没写日志 →「没动静了」
TAIL = 256 * 1024
HEAD = 64 * 1024          # Codex 第一行 session_meta 带整段系统提示，常有几十 KB
MAX_FILES = 40
_CACHE = {}                # path -> (mtime, size, result)


def _read(path, size):
    with open(path, "rb") as f:
        head = f.read(HEAD)
        if size > TAIL:
            f.seek(size - TAIL)
        else:
            f.seek(0)
        tail = f.read(TAIL)
    return head, tail


def _lines(blob, skip_first):
    parts = blob.split(b"\n")
    if skip_first and len(parts) > 1:
        parts = parts[1:]      # 从中间切开的第一行不完整
    for p in parts:
        p = p.strip()
        if not p:
            continue
        try:
            d = json.loads(p)
        except ValueError:
            continue
        if isinstance(d, dict):
            yield d


def _ts(v):
    if isinstance(v, (int, float)):
        return float(v) / (1000.0 if v > 1e12 else 1.0)
    if isinstance(v, str) and len(v) >= 19:
        try:
            from datetime import datetime
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _one(s, n):
    s = " ".join(str(s or "").split())
    return (s[: n - 1] + "…") if len(s) > n else s


def _human_prompt(text):
    """用户真正说的话：去掉 Agent 自己塞进来的环境说明、命令标签之类。"""
    t = str(text or "").strip()
    if not t or t.startswith("<") or t.startswith("Caveat:") or t.startswith("[Request interrupted"):
        return ""
    return t


def _base(p):
    return os.path.basename(str(p or "").rstrip("/")) or str(p or "")


# ---------------- Codex：~/.codex/sessions/年/月/日/rollout-*.jsonl ----------------
def _codex_step(p):
    t = p.get("type")
    if t == "function_call" or t == "custom_tool_call":
        name = p.get("name") or ""
        try:
            a = json.loads(p.get("arguments") or "{}") if isinstance(p.get("arguments"), str) else (p.get("arguments") or {})
        except ValueError:
            a = {}
        if t == "custom_tool_call" and name == "exec":
            # 新版 Codex 的 exec 收的是一段 JS（const r = await tools.exec_command({cmd: "..."})），把里面的命令抠出来
            raw = str(p.get("input") or "")
            m = re.search(r"""(?:cmd|command)\s*:\s*(\[[^\]]*\]|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`[^`]*`)""", raw) or \
                re.search(r"""exec_command\(\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`[^`]*`)""", raw)
            if m:
                cmd = m.group(1)
                if cmd.startswith("["):
                    cmd = " ".join(x for x in re.findall(r"""["'`]([^"'`]*)["'`]""", cmd) if x not in ("bash", "-lc", "zsh", "-c"))
                else:
                    cmd = cmd[1:-1].replace('\\"', '"').replace("\\n", " ")
                return "运行：" + _one(cmd, 70)
            first = raw.strip().splitlines()[0] if raw.strip() else ""
            return "运行命令" if (not first or re.match(r"\s*(const|let|var|await|import|function)\b", first)) else "运行：" + _one(first, 70)
        if name in ("request_user_input", "request_user_input_async"):
            return "在等你回复"
        if name in ("shell", "container.exec", "exec_command", "local_shell"):
            cmd = a.get("command") or a.get("cmd") or ""
            if isinstance(cmd, list):
                cmd = " ".join(str(x) for x in cmd[-2:] if x not in ("bash", "-lc", "zsh", "-c"))
            return "运行：" + _one(cmd, 70)
        if name == "apply_patch" or (t == "custom_tool_call" and "patch" in name):
            raw = p.get("input") or a.get("input") or ""
            files = [ln.split(":", 1)[1].strip() for ln in str(raw).splitlines() if ln.startswith("*** ") and ":" in ln and "File" in ln]
            return "改文件：" + _one("、".join(_base(f) for f in files[:3]) or "代码", 70)
        if name == "update_plan":
            return None
        return "用工具：" + _one(name, 40)
    return None


def _codex(path, head, tail, size):
    r = {"agent": "codex", "cwd": "", "task": "", "step": "", "log": [], "state": None, "plan": None, "started": None, "done_at": None}
    for d in _lines(head, False):
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        if d.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "user" and not r.get("first"):
            for c in p.get("content") or []:
                if isinstance(c, dict) and _human_prompt(c.get("text")):
                    r["first"] = c["text"]
                    break
    # 第一行 session_meta 可能比开头读的还长、解析不了整行，所以用正则直接找要的几个字段
    first_line = head.split(b"\n", 1)[0]
    m = re.search(rb'"thread_source"\s*:\s*"([^"]*)"', first_line)
    # 桌面版会另开「自动审批 / 审查」之类的子会话（thread_source 不是 user，或 source 是 subagent），不是你派的任务，不显示
    if (m and m.group(1) != b"user") or re.search(rb'"source"\s*:\s*\{\s*"subagent"', first_line):
        r["skip"] = True
    m = re.search(rb'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"', first_line)
    if m:
        try:
            r["cwd"] = json.loads(b'"' + m.group(1) + b'"')
        except ValueError:
            pass
    m = re.search(rb'"timestamp"\s*:\s*"([^"]+)"', first_line)
    r["started"] = _ts(m.group(1).decode("utf-8", "replace")) if m else None
    last_start = last_done = last_user = None
    for d in _lines(tail, size > TAIL):
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        ts = _ts(d.get("timestamp"))
        typ, pt = d.get("type"), p.get("type")
        if typ == "session_meta" and not r["cwd"]:
            r["cwd"] = str(p.get("cwd") or "")
        elif typ == "turn_context" and p.get("cwd"):
            r["cwd"] = str(p["cwd"])
        elif typ == "event_msg" and pt == "task_started":
            last_start, r["log"], r["plan"] = ts, [], None
        elif typ == "event_msg" and pt == "task_complete":
            last_done = ts
        elif typ == "event_msg" and pt == "user_message":
            u = _human_prompt(p.get("message"))
            if u:
                r["task"], r["task_at"], last_user = u, ts, ts
                if last_start is None or (ts and last_start and ts > last_start + 5):
                    r["log"], r["plan"] = [], None
        elif typ == "event_msg" and pt == "agent_message" and p.get("message"):
            r["step"] = _one(p["message"], 80)
            r["log"].append({"t": ts, "s": r["step"]})
        elif typ == "response_item" and pt == "message" and p.get("role") == "user":
            for c in p.get("content") or []:
                u = _human_prompt(c.get("text") if isinstance(c, dict) else "")
                if u:
                    r["task"], r["task_at"], last_user = u, ts, ts
                    break
        elif typ == "response_item" and pt == "message" and p.get("role") == "assistant":
            txt = " ".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict))
            if txt.strip():
                r["step"] = _one(txt, 80)
                r["log"].append({"t": ts, "s": r["step"]})
        elif typ == "response_item":
            if pt == "function_call" and p.get("name") == "update_plan":
                try:
                    plan = json.loads(p.get("arguments") or "{}").get("plan") or []
                    r["plan"] = [(str(x.get("step") or ""), str(x.get("status") or "")) for x in plan if isinstance(x, dict)]
                except (ValueError, AttributeError):
                    pass
                continue
            s = _codex_step(p)
            if s:
                r["step"] = s
                r["log"].append({"t": ts, "s": s})
    # 上一轮的 task_complete 之后你又说了话（新一轮已经开始、task_started 还没写）→ 不算完成，免得闪一下「完成」
    if last_done and (not last_start or last_done >= last_start) and (not last_user or last_done >= last_user):
        r["state"], r["done_at"] = "done", last_done
    return r


# ---------------- Claude Code：~/.claude/projects/<项目>/<会话>.jsonl ----------------
def _claude_step(c):
    name, a = c.get("name") or "", c.get("input") if isinstance(c.get("input"), dict) else {}
    if name == "Bash":
        return "运行：" + _one(a.get("description") or a.get("command"), 70)
    if name in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return ("写文件：" if name == "Write" else "改文件：") + _base(a.get("file_path") or a.get("notebook_path"))
    if name == "Read":
        return "看文件：" + _base(a.get("file_path"))
    if name in ("Grep", "Glob"):
        return "查找：" + _one(a.get("pattern"), 50)
    if name in ("Task", "Agent"):
        return "派子任务：" + _one(a.get("description"), 50)
    if name in ("WebFetch", "WebSearch"):
        return "查网页：" + _one(a.get("query") or a.get("url"), 60)
    if name in ("TodoWrite", "TaskCreate", "TaskUpdate") or name.startswith("mcp__deck_pace"):
        return None
    return "用工具：" + _one(name.replace("mcp__", ""), 40)


def _claude(path, head, tail, size):
    r = {"agent": "claude", "cwd": "", "task": "", "step": "", "log": [], "state": None, "plan": None, "started": None, "done_at": None}
    for d in _lines(head, False):
        if d.get("cwd") and not r["cwd"]:
            r["cwd"] = str(d["cwd"])
            r["started"] = _ts(d.get("timestamp"))
        m = d.get("message") if isinstance(d.get("message"), dict) else {}
        c = m.get("content")
        text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in (c or []) if isinstance(x, dict) and x.get("type") == "text")
        if d.get("type") == "user" and not d.get("isMeta") and _human_prompt(text):
            r["first"] = text   # 文件太大、末尾看不到最近一句话时，至少用会话开头那句
            break
    end_at = None
    for d in _lines(tail, size > TAIL):
        if d.get("isSidechain"):
            continue
        ts = _ts(d.get("timestamp"))
        if d.get("cwd") and not r["cwd"]:
            r["cwd"] = str(d["cwd"])   # 用会话开始时的项目目录，别跟着中途的 cd 变
        if d.get("type") in ("summary", "custom-title", "ai-title") and not r["task"]:
            r["title"] = _one(d.get("summary") or d.get("title") or d.get("customTitle") or "", 80)
        m = d.get("message") if isinstance(d.get("message"), dict) else {}
        if d.get("type") == "user" and not d.get("isMeta"):
            c = m.get("content")
            text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in (c or []) if isinstance(x, dict) and x.get("type") == "text")
            u = _human_prompt(text)
            if u:
                r["task"], r["task_at"], r["log"], r["plan"], end_at = u, ts, [], None, None
            elif isinstance(c, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                end_at = None
        elif d.get("type") == "assistant":
            for c in m.get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    if c.get("name") == "TodoWrite":
                        todos = (c.get("input") or {}).get("todos") or []
                        r["plan"] = [(str(x.get("content") or ""), str(x.get("status") or "")) for x in todos if isinstance(x, dict)]
                    s = _claude_step(c)
                    if s:
                        r["step"] = s
                        r["log"].append({"t": ts, "s": s})
                elif c.get("type") == "text" and c.get("text", "").strip():
                    r["step"] = _one(c["text"], 80)
                    r["log"].append({"t": ts, "s": r["step"]})
            end_at = ts if m.get("stop_reason") == "end_turn" else None
    if end_at:
        r["state"], r["done_at"] = "done", end_at
    return r


def _files(now):
    out = []
    day = 86400
    for d in {time.strftime("%Y/%m/%d", time.localtime(now - k * day)) for k in (0, 1)}:
        out += [("codex", p) for p in glob.glob(os.path.join(CODEX_HOME, "sessions", d, "rollout-*.jsonl"))]
    out += [("claude", p) for p in glob.glob(os.path.join(CLAUDE_HOME, "projects", "*", "*.jsonl"))]
    fresh = []
    for agent, p in out:
        try:
            st = os.stat(p)
        except OSError:
            continue
        if now - st.st_mtime < FRESH:
            fresh.append((st.st_mtime, st.st_size, agent, p))
    fresh.sort(reverse=True)
    return fresh[:MAX_FILES]


def tasks(now=None, names=None):
    """返回和 agentpace.tasks() 同样形状的任务，source='log'，percent 可能是 None。"""
    now = now or time.time()
    names = names or {}
    out, seen = [], set()
    for mtime, size, agent, p in _files(now):
        seen.add(p)
        c = _CACHE.get(p)
        if c and c[0] == mtime and c[1] == size:
            r = c[2]
        else:
            try:
                head, tail = _read(p, size)
                r = (_codex if agent == "codex" else _claude)(p, head, tail, size)
            except (OSError, ValueError, TypeError, AttributeError):
                continue
            _CACHE[p] = (mtime, size, r)
        if r.get("skip") or (not r.get("task") and not r.get("log")) or str(r.get("task") or r.get("first") or "").startswith("The following is the Codex agent history"):
            continue
        done = r.get("state") == "done"
        at = r.get("done_at") if done else mtime
        state = "done" if done else ("running" if now - mtime < QUIET else "quiet")
        plan = r.get("plan") or []
        pct = None
        if plan:
            fin = sum(1 for _, s in plan if s == "completed")
            pct = int(round(100.0 * fin / len(plan)))
            if pct >= 100 and not done:
                pct = 99
        if done and pct is not None and pct < 99:
            done, state = False, "quiet"   # 这一轮停下了但清单没做完：多半在等你回复，不算完成
        elif done:
            pct = 100
        log = []
        for x in r["log"]:
            if x.get("t") and not (log and log[-1]["s"] == x["s"]):
                log.append({"t": x["t"], "p": -1, "s": x["s"]})   # 日志里每一步没有自己的百分比，别把现在的数安到旧步骤上
        log = log[-20:]
        since = r.get("task_at") or r.get("started")
        out.append({"id": "log-%s-%s" % (agent, hashlib.sha1(p.encode()).hexdigest()[:10]), "agent": agent, "agent_name": names.get(agent, agent),
                    "percent": pct, "pct_from": "plan" if plan else None, "state": state, "source": "log",
                    "task": _one(r.get("task") or r.get("title") or r.get("first"), 80) or None, "step": _one(r.get("step"), 80) or None,
                    "project": _base(r.get("cwd"))[:60], "cwd": r.get("cwd") or "", "at": at or mtime, "since": since, "pid": None, "log": log})
    for k in [k for k in _CACHE if k not in seen]:
        _CACHE.pop(k, None)
    return out
