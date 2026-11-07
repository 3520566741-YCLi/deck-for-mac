"""Vesper 清理：Claude、DeepSeek（Harness）、Codex 工作区里没用的东西（旧测试残留、日志、缓存、临时目录）。

铁律：
- 只移进废纸篓（~/.Trash），绝不直接删除。每次移动都记下原位置，可以一键撤销。
- 先扫描、列清单、出确认卡；用户 点「移到废纸篓」或口头同意后才动手。
- 每晚 23:30 Deck 开着且联网时自动扫描一次并弹确认卡；没人确认就跳过。
- 明确不碰：Claude 虚拟机镜像、Fusion 安装包、Harness「6-官方端基线」、Codex/2026-10-31（用户说先不动）。

扫描只读文件信息（大小、修改时间），不读内容。只用标准库，兼容 macOS 自带 python3 (3.9+)。
"""
import fnmatch
import glob
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid

HOME = os.path.expanduser("~")
TRASH = os.path.join(HOME, ".Trash")
LOG = os.path.join(HOME, "Library", "Logs", "weekly-cache-clean.log")  # 文件名沿用以前的每周清理，「每日清理」卡片读它
DAY = 86400

# 用户说先不动的大目录：路径里含这些就跳过
PROTECT = ["claude-code-vm", "vm_bundles", "Fusion", "6-官方端基线", "Codex/2026-10-31"]

# 测试残留：在这些项目目录里找（不进 node_modules / .git / .venv / *.app，最多 5 层）
PROJECT_ROOTS = ["~/Desktop", "~/Documents", "~/deck-for-mac"]
# 只认这些「一看就是测试/临时产物」的名字；源码里的 Tests、test 目录不算（11-05 盘点时发现会误中）
TEST_NAMES = ["test-results", "playwright-report", "coverage", ".nyc_output", ".pytest_cache", "*-tmp", "tmp-*", "tmp",
              "*test-home", "resume-test", "*scratch*", ".probe-*", ".research_cache", "*.bak", "*.bak-*", "*.orig"]
# 用户自己起名的测试文件夹（名字带「测试」），默认不勾，名字带「记录」的不算（还在用）
TEST_DIRS_CN = ["*测试库", "测试文件", "*测试文件夹", "*-测试", "测试-*"]
# 永远不碰：Claude 的长期记忆、凭据
NEVER_PARTS = ["/memory/", "/skills/", "/.git/"]
NEVER_NAMES = ["memory", "skills", "settings.json", "config.toml", ".credentials.yaml", ".credentials.json", ".claude.json"]

# 每条规则：分组、说明、要找的路径（glob，找到的每一项单独移动）、多少天没改过才算没用、默认勾不勾。
# 依据 11-05 在用户的 Mac 上做的只读盘点。
RULES = [
    # ---- Claude ----
    {"id": "claude-cli-cache", "group": "Claude", "label": "Claude Code 缓存和 MCP 日志", "paths": ["~/Library/Caches/claude-cli-nodejs/*/*"], "days": 7, "on": True},
    {"id": "claude-telemetry", "group": "Claude", "label": "Claude Code 没发出去的统计记录", "paths": ["~/.claude/telemetry/*"], "days": 7, "on": True},
    {"id": "claude-session-env", "group": "Claude", "label": "Claude Code 旧会话留下的空文件夹", "paths": ["~/.claude/session-env/*"], "days": 7, "on": True},
    {"id": "claude-file-history", "group": "Claude", "label": "Claude Code 旧会话的改动备份", "paths": ["~/.claude/file-history/*"], "days": 30, "on": True},
    {"id": "claude-uploads", "group": "Claude", "label": "Claude Code 旧会话里上传的文件", "paths": ["~/.claude/uploads/*"], "days": 30, "on": True},
    {"id": "claude-orphans", "group": "Claude", "label": "文件夹已经不在的 Claude Code 会话记录", "paths": ["~/.claude/projects/*"], "days": 7, "on": True, "orphan": True},
    {"id": "claude-baks", "group": "Claude", "label": "Claude 旧设置备份", "paths": ["~/.claude/*.bak*", "~/.claude/*backup", "~/.claude/backups/*"], "days": 30, "on": True},
    {"id": "claude-app-logs", "group": "Claude", "label": "Claude 桌面版旧日志", "paths": ["~/Library/Logs/Claude/*"], "days": 14, "on": True},
    {"id": "claude-old-versions", "group": "Claude", "label": "Claude 桌面版里旧版本的 Claude Code", "paths": ["~/Library/Application Support/Claude/claude-code/*"],
     "days": 0, "on": True, "keep_newest": 1},
    {"id": "claude-app-cache", "group": "Claude", "label": "Claude 桌面版网页缓存（只在 Claude 没打开时清）",
     "paths": ["~/Library/Application Support/Claude/Cache", "~/Library/Application Support/Claude/Code Cache",
               "~/Library/Application Support/Claude/GPUCache", "~/Library/Application Support/Claude/*DawnCache",
               "~/Library/Application Support/Claude/Dawn*Cache", "~/Library/Caches/com.anthropic.*"],
     "days": 0, "on": True, "skip_running": ["-x", "Claude"]},
    {"id": "claude-tmp", "group": "Claude", "label": "Claude 测试用临时目录", "paths": ["/private/tmp/claude*", "$TMPDIR/claude*"], "days": 2, "on": True},
    {"id": "claude-sessions", "group": "Claude", "label": "60 天前的 Claude Code 会话记录（清了就不能再接着那次聊）",
     "paths": ["~/.claude/projects/*/*.jsonl"], "days": 60, "on": False},
    # ---- Codex ----
    {"id": "codex-tmp", "group": "Codex", "label": "Codex 临时文件", "paths": ["~/.codex/tmp/*", "/private/tmp/codex*", "$TMPDIR/codex*", "$TMPDIR/.com.openai.codex.*"], "days": 7, "on": True,
     "skip_running": ["-f", "Codex.app/Contents/MacOS/|/codex( |$)"]},
    {"id": "codex-archived", "group": "Codex", "label": "Codex 已归档的旧会话", "paths": ["~/.codex/archived_sessions/*"], "days": 30, "on": True},
    {"id": "codex-attachments", "group": "Codex", "label": "Codex 旧附件", "paths": ["~/.codex/attachments/*"], "days": 30, "on": True},
    {"id": "codex-baks", "group": "Codex", "label": "Codex 旧设置备份", "paths": ["~/.codex/*.bak", "~/.codex/*.bak-*"], "days": 30, "on": True},
    {"id": "codex-sessions", "group": "Codex", "label": "60 天前的 Codex 会话记录（清了就不能再接着那次聊）",
     "paths": ["~/.codex/sessions/*/*/*"], "days": 60, "on": False},
    {"id": "codex-images", "group": "Codex", "label": "Codex 30 天前生成的图片", "paths": ["~/.codex/generated_images/*"], "days": 30, "on": False},
    # ---- DeepSeek（Harness，~/.dsh）----
    {"id": "dsh-installers", "group": "DeepSeek", "label": "DeepSeek Harness 旧安装包", "paths": ["~/.dsh/cache/*"], "days": 7, "on": True, "keep_newest": 2},
    {"id": "dsh-backups", "group": "DeepSeek", "label": "DeepSeek Harness 旧备份", "paths": ["~/.dsh/guard-backups/*", "~/.dsh/delegation-backups/*", "~/.dsh/*.bak*"],
     "days": 30, "on": False, "keep_newest": 1},  # 各版本的回滚备份，默认不勾
    {"id": "dsh-logs", "group": "DeepSeek", "label": "DeepSeek Harness 旧日志", "paths": ["~/.dsh/logs/*", "~/.dsh/log/*"], "days": 14, "on": True},
    {"id": "dsh-sessions", "group": "DeepSeek", "label": "30 天前的 DeepSeek Harness 会话", "paths": ["~/.dsh/sessions/*"], "days": 30, "on": False},
    # ---- 测试残留 ----
    {"id": "test-tmp", "group": "测试残留", "label": "测试工具留下的临时目录（Playwright、pytest 等）",
     "paths": ["/private/tmp/playwright*", "$TMPDIR/playwright*", "/private/tmp/pytest*", "$TMPDIR/pytest-of-*"], "days": 2, "on": True},
    {"id": "test-leftovers", "group": "测试残留", "label": "项目里的旧测试产物（临时目录、测试报告、.bak 备份）", "find": TEST_NAMES, "days": 14, "on": True},
    {"id": "test-logs", "group": "测试残留", "label": "项目里 30 天没动过的大日志（超过 1 MB）", "find": ["*.log"], "min_bytes": 1048576, "days": 30, "on": True},
    {"id": "test-dirs", "group": "测试残留", "label": "名字带「测试」的旧文件夹（请先看一眼）", "find": TEST_DIRS_CN, "dirs_only": True, "days": 7, "on": False, "any_dir": True},
    # ---- 旧安装包（：已经不用了的安装包也清；每晚由 DeepSeek 再核对一遍）----
    {"id": "installers", "group": "安装包", "label": "下载里 3 天前的安装包（.dmg / .pkg）",
     "paths": ["~/Downloads/*.dmg", "~/Downloads/*.pkg", "~/Downloads/*.xip", "~/Desktop/*.dmg", "~/Desktop/*.pkg"], "days": 3, "on": True},
    {"id": "updater-staging", "group": "安装包", "label": "应用自动更新留下的旧安装文件",
     "paths": ["~/Library/Caches/*/org.sparkle-project.Sparkle/Installation/*", "~/Library/Caches/*.ShipIt/*"], "days": 1, "on": True},
    # ---- 系统数据里的两个已知泄漏（11-06 查「系统数据」150GB 时找到的）----
    # Chrome 每次启动在 $TMPDIR 旁边的 X 目录里克隆一份自己（约 1.4GB），非正常退出就不删；正在用的那份（lsof 看得到）跳过
    {"id": "chrome-code-sign-clone", "group": "系统数据", "label": "Chrome 没删掉的自我副本（code_sign_clone，正在用的那份不动）",
     "paths": ["$TMPDIR/../X/com.google.Chrome.code_sign_clone/code_sign_clone.*"], "days": 0, "on": True, "safe": True, "skip_in_use": True},
    # Codex 插件市场升级的暂存克隆（11-06 一晚上涨到 72GB）。用户：Codex 这里要先问他，所以默认不勾，只在卡片上提醒
    {"id": "codex-staging", "group": "系统数据", "label": "Codex 插件市场升级留下的暂存克隆（先问用户）",
     "paths": ["~/.codex/.tmp/marketplaces/.staging/marketplace-*"], "days": 0, "on": False},
    # ---- 缓存（原来每周日 12:00 的 weekly-cache-clean.sh 那份白名单，11-06 并进每日清理）----
    {"id": "dev-caches", "group": "缓存", "label": "开发工具的下载缓存（uv、pip、npm、pnpm、Homebrew 等，用到会自动重新下载）",
     "paths": ["~/.cache/uv", "~/Library/pnpm/store", "~/.npm/_cacache", "~/Library/Caches/pip", "~/Library/Caches/Homebrew",
               "~/Library/Caches/electron", "~/Library/Caches/electron-builder", "~/Library/Caches/node-gyp",
               "~/Library/Caches/ms-playwright-mcp", "~/Library/Caches/JetBrains"], "days": 0, "on": True, "safe": True},
    {"id": "updater-caches", "group": "缓存", "label": "应用更新器的缓存（OpenCode、DeepSeek、Notion）",
     "paths": ["~/Library/Caches/@opencodedesktop-updater", "~/Library/Caches/@deepseek-aidsh-desktop-updater", "~/Library/Caches/notion-updater"],
     "days": 0, "on": True, "safe": True},
    {"id": "chrome-cache", "group": "缓存", "label": "Chrome 网页缓存（只在 Chrome 没打开时清）", "paths": ["~/Library/Caches/Google/Chrome"],
     "days": 0, "on": True, "safe": True, "skip_running": ["-x", "Google Chrome"]},
]


def _x(p):
    p = p.replace("$TMPDIR", (os.environ.get("TMPDIR") or tempfile.gettempdir()).rstrip("/"))
    return os.path.expanduser(p)


def _home(p):
    return "~" + p[len(HOME):] if p.startswith(HOME) else p


def _protected(p):
    name = os.path.basename(p.rstrip("/"))
    return any(k in p for k in PROTECT) or any(k in p + "/" for k in NEVER_PARTS) or name in NEVER_NAMES or "记录" in name \
        or os.path.realpath(p) in (HOME, TRASH, "/", "/private/tmp")


def _stat_tree(p, limit=20000):
    """(总字节, 最近修改时间, 文件数)。不跟随软链接；文件太多时只看前 limit 个。"""
    try:
        st = os.lstat(p)
    except OSError:
        return 0, 0, 0
    if not os.path.isdir(p) or os.path.islink(p):
        return st.st_size, st.st_mtime, 1
    size, newest, n = 0, 0, 0  # 文件夹看里面文件的最近修改时间；空文件夹才看它自己的
    for root, dirs, files in os.walk(p):
        for f in files:
            try:
                s = os.lstat(os.path.join(root, f))
            except OSError:
                continue
            size += s.st_size
            newest = max(newest, s.st_mtime)
            n += 1
            if n >= limit:
                return size, newest, n
    return size, newest or st.st_mtime, n


def _git_tracked(p):
    d = p if os.path.isdir(p) else os.path.dirname(p)
    try:
        r = subprocess.run(["git", "-C", d, "ls-files", "--error-unmatch", p], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _find(patterns, dirs_only=False):
    out = []
    for root in PROJECT_ROOTS:
        root = _x(root)
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("/").count("/")
        for cur, dirs, files in os.walk(root):
            if _protected(cur):
                dirs[:] = []
                continue
            depth = cur.count("/") - base_depth
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", ".venv", "venv", "Library") and not d.endswith(".app")]
            for name in list(dirs) + ([] if dirs_only else files):
                if any(fnmatch.fnmatch(name, pat) for pat in patterns):
                    out.append(os.path.join(cur, name))
                    if name in dirs:
                        dirs.remove(name)
            if depth >= 5:
                dirs[:] = []
    return out


SKIP_PARTS = ["/profile-archive/"]  # 有意留的配置快照，拿掉 .bak 会让快照不完整
MARKERS = (".git", "package.json", "pyproject.toml", "Package.swift", "Cargo.toml", "go.mod", "Makefile", "requirements.txt")


def _in_project(p):
    """find 规则只清开发项目里的测试残留：往上找到项目标记（.git、package.json…）才算；课程资料等普通文件夹里的 tmp 不碰。"""
    if any(x in p + "/" for x in SKIP_PARTS):
        return False
    if "/.pnpm-store/" in p:
        return True
    d = os.path.dirname(p)
    roots = [_x(r).rstrip("/") for r in PROJECT_ROOTS]
    while len(d) > 1 and d not in roots and d != HOME:
        if any(os.path.exists(os.path.join(d, m)) for m in MARKERS):
            return True
        d = os.path.dirname(d)
    return False


def _orphan(project_dir):
    """Claude Code 的会话文件夹：读第一条记录里的 cwd（只看这一个字段），那个文件夹已经不在了就算没用。"""
    for fn in sorted(glob.glob(os.path.join(project_dir, "*.jsonl")))[:3]:
        try:
            with open(fn, encoding="utf-8", errors="replace") as f:
                for _ in range(20):
                    line = f.readline()
                    if not line:
                        break
                    i = line.find('"cwd":"')
                    if i >= 0:
                        cwd = line[i + 7:line.index('"', i + 7)]
                        return not os.path.exists(cwd)
        except (OSError, ValueError):
            continue
    return False


def _running(args):
    """args 例如 ["-x", "Claude"]（按进程名）或 ["-f", "正则"]（按完整命令行）。"""
    try:
        # -a：把自己的祖先进程也算上（macOS 的 pgrep 默认排除祖先；管家若是从 Claude 里启动的，不加会漏判）
        flags = ["-a"] if sys.platform == "darwin" else []
        return subprocess.run(["pgrep"] + flags + list(args), capture_output=True, timeout=3).returncode == 0
    except Exception:  # noqa: BLE001
        return True  # 不确定就当作在运行，不清


def _in_use(paths):
    """lsof 看哪些路径（目录）底下有打开的文件。lsof 出错就当全部在用，不清。"""
    if not paths:
        return set()
    parents = sorted({os.path.dirname(p) for p in paths})
    try:
        out = subprocess.run(["lsof", "-Fn", "+D"] + parents, capture_output=True, text=True, timeout=60).stdout
    except Exception:  # noqa: BLE001
        return set(paths)
    opened = [ln[1:] for ln in out.splitlines() if ln.startswith("n/")]
    real = {p: os.path.realpath(p) + "/" for p in paths}
    return {p for p, rp in real.items() if any(os.path.realpath(o).startswith(rp) or o.startswith(p + "/") for o in opened)}


def scan(rules=None):
    """扫描，返回清理计划（不动任何文件）。rules 默认是上面的 RULES；工具箱的「垃圾清理」传自己的规则。"""
    now = time.time()
    plan = {"id": uuid.uuid4().hex[:12], "at": now, "rules": []}
    seen = set()
    for r in (RULES if rules is None else rules):
        if r.get("skip_running") and _running(r["skip_running"]):
            continue
        if r.get("find"):
            cands = _find(r["find"], r.get("dirs_only"))
        else:
            cands = []
            for pat in r["paths"]:
                cands += glob.glob(_x(pat))
        cands = sorted(set(cands))
        if r.get("skip_in_use"):
            busy = _in_use(cands)
            cands = [p for p in cands if p not in busy]
        if r.get("keep_newest"):
            keep = sorted(cands, key=lambda p: os.lstat(p).st_mtime if os.path.lexists(p) else 0)[-r["keep_newest"]:]
            cands = [p for p in cands if p not in keep]
        items = []
        for p in cands:
            rp = os.path.realpath(p)
            if rp in seen or _protected(p) or os.path.islink(p):
                continue
            if r.get("orphan") and not _orphan(p):
                continue
            size, newest, n = _stat_tree(p)
            if now - newest < r["days"] * DAY or size < r.get("min_bytes", 0):
                continue
            if r.get("find") and ((not r.get("any_dir") and not _in_project(p)) or _git_tracked(p)):
                continue
            seen.add(rp)
            items.append({"path": p, "bytes": size, "files": n, "age_days": int((now - newest) / DAY)})
        if items:
            items.sort(key=lambda i: -i["bytes"])
            plan["rules"].append({"id": r["id"], "group": r["group"], "label": r["label"], "on": r["on"], "days": r["days"], "safe": bool(r.get("safe")),
                                  "count": len(items), "bytes": sum(i["bytes"] for i in items),
                                  "examples": [_home(i["path"]) for i in items[:4]], "items": items})
    plan["bytes"] = sum(x["bytes"] for x in plan["rules"] if x["on"])
    return plan


TRASH_LOCK = threading.Lock()  # 选名字 + 改名要一起做，免得两次清理同时用上同一个名字、后一个盖掉前一个


def move_to_trash(src, check=None):
    """把 src 移进废纸篓并返回新路径。只用 rename（同一个磁盘内的移动）；
    跨磁盘时不复制（会把外接盘的东西复制到内置盘），直接报错，交给访达处理。check(src) 返回原因字符串时拒绝。"""
    why = check(src) if check else None
    if why:
        raise OSError(1, why)
    with TRASH_LOCK:
        dst = _trash_target(src)
        if os.path.lexists(dst):
            raise OSError(17, "废纸篓里已有同名项目")
        try:
            os.rename(src, dst)
        except OSError as e:
            if e.errno == 18:
                raise OSError(18, "在另一个磁盘上，请在访达里拖到废纸篓")
            raise
    return dst


def _trash_target(p):
    name = os.path.basename(p.rstrip("/")) or "item"
    dst = os.path.join(TRASH, name)
    if os.path.exists(dst):
        stem, ext = os.path.splitext(name)
        dst = os.path.join(TRASH, "%s %s%s" % (stem, time.strftime("%H.%M.%S"), ext))
        k = 2
        while os.path.exists(dst):
            dst = os.path.join(TRASH, "%s %s-%d%s" % (stem, time.strftime("%H.%M.%S"), k, ext))
            k += 1
    return dst


def _log(lines):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), ln))
    except OSError:
        pass


def run(plan, rule_ids, rules=None, log_title="开始清理（Vesper，你确认后执行）", tag="Vesper 清理", check=None):
    """把选中规则里的东西移到废纸篓（只移动，不删除）。返回 {moved:[{src,dst,bytes}], failed:[path], bytes}。"""
    os.makedirs(TRASH, exist_ok=True)
    moved, failed, logs = [], [], [log_title]
    for r in plan["rules"]:
        if r["id"] not in rule_ids:
            continue
        rule = next((x for x in (RULES if rules is None else rules) if x["id"] == r["id"]), {})
        if rule.get("skip_running") and _running(rule["skip_running"]):
            logs.append("跳过「%s」：相关应用正在运行" % r["label"])
            continue
        busy = _in_use([it["path"] for it in r["items"]]) if rule.get("skip_in_use") else set()
        for it in r["items"]:
            src = it["path"]
            if not os.path.lexists(src) or _protected(src) or src in busy:
                continue
            try:
                dst = move_to_trash(src, check)
                moved.append({"src": src, "dst": dst, "bytes": it["bytes"]})
                logs.append("移到废纸篓：%s -> %s (%dM)" % (src, dst, round(it["bytes"] / 1048576)))
            except OSError as e:
                failed.append(src)
                logs.append("失败：%s（%s）" % (src, e.strerror or e))
    total = sum(m["bytes"] for m in moved)
    logs.append("完成：移走 %d 项，共 %dM；%s" % (len(moved), round(total / 1048576), tag))
    _log(logs)
    return {"moved": moved, "failed": failed, "bytes": total}


def undo(moved):
    """把刚移进废纸篓的东西放回原处（原位置已有同名东西就跳过）。"""
    back, skipped = 0, []
    for m in moved:
        if os.path.lexists(m["dst"]) and not os.path.lexists(m["src"]):
            try:
                os.makedirs(os.path.dirname(m["src"]), exist_ok=True)
                with TRASH_LOCK:
                    if os.path.lexists(m["src"]):
                        raise OSError(17, "原位置已有东西")
                    os.rename(m["dst"], m["src"])
                back += 1
                continue
            except OSError:
                pass
        skipped.append(m["src"])
    _log(["撤销 Vesper 清理：放回 %d 项" % back])
    return {"restored": back, "skipped": skipped}


def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%d %s" if u in ("B", "KB") or n >= 100 else "%.1f %s") % (n, u)
        n /= 1024


def summary(plan):
    """给模型看的精简版（不含每个文件）。"""
    return {"id": plan["id"], "total_mb": round(plan["bytes"] / 1048576, 1), "rules": [
        {"id": r["id"], "group": r["group"], "label": r["label"], "default_on": r["on"], "count": r["count"],
         "mb": round(r["bytes"] / 1048576, 1), "examples": r["examples"][:2]} for r in plan["rules"]]}


def public(plan):
    """给页面画确认卡用。"""
    return {"id": plan["id"], "at": plan["at"], "bytes": plan["bytes"], "rules": [
        {k: r[k] for k in ("id", "group", "label", "on", "days", "count", "bytes", "examples")} for r in plan["rules"]]}


if __name__ == "__main__":
    print(json.dumps(summary(scan()), ensure_ascii=False, indent=1))
