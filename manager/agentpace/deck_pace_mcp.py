"""Deck Agent 进度上报工具（本机 stdio MCP 服务，只用 Python 标准库）。

做法借鉴开源项目 Codex Pacer（MIT，github.com/RyanZhangNTU/codex-pacer）：干活的 Agent 最清楚自己做到哪了，
所以给 Claude Code / Codex / DeepSeek Harness 各装一个只有一个工具的小服务 report_progress，Agent 自己估计
「这个请求完成了百分之几、现在在做哪一步」，Deck 把它画成进度环。

- 每个 Agent 会话各起一个本进程（Codex 桌面版是一个后台进程里开多个会话，各自也是单独一个本进程），
  所以用本进程号区分会话，本进程退出 = 会话结束；
- 只往 ~/Library/Application Support/Deck/pace/ 写一个很小的 JSON（百分比、任务名、当前步骤、工作目录名、时间），
  不联网、不读任何别的文件、不碰提示词和回复内容；
- 开关文件 enabled-<agent> 不在时，工具列表为空、调用一律拒绝：Deck 里一关就立即失效，不用重启会话；
- 会话结束（stdin 关闭）时把记录标成 ended，Deck 过一会儿自己清掉；Codex 是直接杀掉本进程，
  这时靠 Deck 检查进程号判断会话已经结束。

此文件由 Deck 安装到 ~/Library/Application Support/Deck/pace/deck_pace_mcp.py，Deck 更新时会覆盖。
"""
import json
import os
import sys
import time

OWNER = "deck-pace-v1"  # 安装器靠这一行认出是自己的文件
AGENTS = ("claude", "codex", "deepseek")
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_LINE = 256 * 1024

INSTRUCTIONS = (
    "Deck shows the user a live progress ring for each agent task. Report your own best estimate of how much of the "
    "user's current request is done with report_progress: in the same batch as your first useful tool calls (with a "
    "short task title), after each meaningful step (when your estimate moved by about 10 points or more), and before "
    "you stop to wait for background work. Call it with percent 100 right before your final answer once the request "
    "is fully done. Skip it for short replies that need no tools. Never make a separate turn just to report. "
    "Write task and step in the user's language."
)

TOOL = {
    "name": "report_progress",
    "description": INSTRUCTIONS,
    "inputSchema": {
        "type": "object",
        "properties": {
            "percent": {"type": "integer", "minimum": 0, "maximum": 100,
                        "description": "Best estimate of how much of the current request is done (0-100). It may go down when you find more work."},
            "task": {"type": "string", "maxLength": 40,
                     "description": "Very short title of the user's current request, e.g. 'Fix Safari export button'. Keep it the same for the whole request."},
            "step": {"type": "string", "maxLength": 60,
                     "description": "What you are doing right now, a few words, e.g. 'Running tests'."},
        },
        "required": ["percent"],
        "additionalProperties": False,
    },
    "annotations": {"title": "Report progress to Deck", "readOnlyHint": True, "destructiveHint": False,
                    "idempotentHint": True, "openWorldHint": False},
}


def _agent():
    a = sys.argv[sys.argv.index("--agent") + 1] if "--agent" in sys.argv[:-1] else ""
    return a if a in AGENTS else "claude"


AGENT = _agent()
PID, PPID = os.getpid(), os.getppid()
FLAG = os.path.join(HERE, "enabled-" + AGENT)
REPORT = os.path.join(HERE, "reports", "%s-%d.json" % (AGENT, PID))
STATE = {"started": time.time()}


def enabled():
    try:
        return os.path.isfile(FLAG)
    except OSError:
        return False


def _clean(s, n):
    if not isinstance(s, str):
        return None
    s = " ".join(s.replace("\x00", " ").split())[:n]
    return s or None


def write(extra):
    rec = {"agent": AGENT, "pid": PID, "ppid": PPID, "cwd": os.path.basename(os.getcwd().rstrip("/")) or "~",
           "started": STATE["started"], "at": time.time()}
    rec.update(STATE.get("last") or {})
    rec.update(extra)
    try:
        os.makedirs(os.path.dirname(REPORT), mode=0o700, exist_ok=True)
        tmp = REPORT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, REPORT)
    except OSError:
        pass  # 磁盘满、目录没权限：只是看不到进度，绝不影响 Agent 干活


def call(args):
    if not enabled():
        return "Deck progress reporting is turned off.", True
    if not isinstance(args, dict) or set(args) - {"percent", "task", "step"}:
        return "Expected percent (integer 0-100), optional task and step.", True
    p = args.get("percent")
    if type(p) is not int or not 0 <= p <= 100:  # noqa: E721  布尔、小数、字符串都不收
        return "percent must be a whole number from 0 to 100.", True
    last = STATE.get("last") or {}
    task = _clean(args.get("task"), 40)
    # 新任务：换了任务名，或者上一个已经 100 之后又从低处开始
    new = bool((task and task != last.get("task")) or (last.get("percent") == 100 and p < 100))
    rec = {"percent": p, "step": _clean(args.get("step"), 60) or (None if new else last.get("step")), "ended": False}
    rec["task"] = task or (None if new else last.get("task"))
    rec["task_started"] = time.time() if new or not last.get("task_started") else last["task_started"]
    # 这个任务每一步的记录（Deck 详情页显示「干到哪了」，也给 DeepSeek 总结用），只留最近 20 条
    log = [] if new else list(last.get("log") or [])
    if not log or log[-1].get("p") != p or log[-1].get("s") != rec["step"]:
        log.append({"t": round(time.time()), "p": p, "s": rec["step"]})
    rec["log"] = log[-20:]
    STATE["last"] = rec
    write({})
    return "ok", False


def reply(ident, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": ident}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def answer(method, params):
    if method == "initialize":
        v = params.get("protocolVersion")
        res = {"protocolVersion": v if v in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25") else "2025-06-18",
               "capabilities": {"tools": {}}, "serverInfo": {"name": "deck_pace", "version": "1.0.0"}}
        if enabled():
            res["instructions"] = INSTRUCTIONS
        return res
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [TOOL] if enabled() else []}
    if method == "tools/call":
        if params.get("name") != TOOL["name"]:
            return {"content": [{"type": "text", "text": "unknown tool"}], "isError": True}
        text, bad = call(params.get("arguments"))
        return {"content": [{"type": "text", "text": text}], "isError": bad}
    return None


def main():
    for line in sys.stdin.buffer:
        if len(line) > MAX_LINE:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict) or "id" not in msg:
            continue  # 通知（notifications/initialized 等）不用回
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        res = answer(msg.get("method"), params)
        if res is None:
            reply(msg["id"], error={"code": -32601, "message": "method not found"})
        else:
            reply(msg["id"], res)


if __name__ == "__main__":
    os.umask(0o077)  # 报告只给自己看（目录 700、文件 600）
    try:
        main()
    except (OSError, KeyboardInterrupt, ValueError):
        pass
    finally:
        if STATE.get("last"):
            write({"ended": True, "at": time.time()})
