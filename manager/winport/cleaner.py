"""Windows 版 Vesper 清理（11-08 第二步）：Claude、DeepSeek（Harness）、Codex 和开发工具没用的东西（旧测试残留、日志、缓存、临时目录）。

函数名、参数和返回的数据结构跟 Mac 版 cleaner.py 一模一样，页面、Vesper、每日清理都不用改。

铁律（用户 11-05，Windows 上一样）：
- 只移进回收站，绝不直接删除。每次移动都记下回收站里的位置，可以一键撤销（也能在资源管理器的回收站里「还原」）。
- 先扫描、列清单、出确认卡；用户 点「移到回收站」或口头同意后才动手。
- 明确不碰：虚拟机镜像、Fusion、Harness「6-官方端基线」、Codex/2026-10-31、Windows 系统目录、页面文件和休眠文件。

和 Mac 版的区别只在系统层：
- 路径换成 Windows 的（%APPDATA%、%LOCALAPPDATA%、%TEMP%、下载文件夹）；
- 移动用 common.recycle（系统回收站），撤销用 common.restore；
- 「应用在不在运行」「文件有没有被打开」用 psutil，不用 pgrep / lsof。
扫描只读文件信息（大小、修改时间），不读内容。标准库 + psutil。
不在 Windows 上（Linux 上 DECK_OS=win 开发）时也能导入和扫描，只是移不动任何东西。
"""
import fnmatch
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import deckos

from . import common

IS_WIN = sys.platform == "win32"
HOME = os.path.expanduser("~")
TRASH = None  # Windows 没有固定的废纸篓文件夹：每个盘有自己的回收站，见 common.bin_dir
# 文件名沿用 Mac 版（以前的每周清理），「每日清理」卡片读它
LOG = deckos.support("Deck", "logs", "weekly-cache-clean.log")
DAY = 86400
TEMP = (os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()).rstrip("\\/")

# 用户说先不动的大目录、Windows 系统目录：路径里含这些就跳过（路径统一换成 / 再比）
PROTECT = ["claude-code-vm", "vm_bundles", "Fusion", "6-官方端基线", "Codex/2026-10-31",
           "/$Recycle.Bin", "/Windows/", "/Program Files", "/ProgramData/", "/System Volume Information"]

# 测试残留：在这些项目目录里找（不进 node_modules / .git / .venv / AppData，最多 5 层）
PROJECT_ROOTS = ["~/Desktop", "~/Documents", "~/deck-for-mac", "~/source/repos"]
# 只认这些「一看就是测试/临时产物」的名字；源码里的 Tests、test 目录不算（11-05 盘点时发现会误中）
TEST_NAMES = ["test-results", "playwright-report", "coverage", ".nyc_output", ".pytest_cache", "*-tmp", "tmp-*", "tmp",
              "*test-home", "resume-test", "*scratch*", ".probe-*", ".research_cache", "*.bak", "*.bak-*", "*.orig"]
# 用户自己起名的测试文件夹（名字带「测试」），默认不勾，名字带「记录」的不算（还在用）
TEST_DIRS_CN = ["*测试库", "测试文件", "*测试文件夹", "*-测试", "测试-*"]
# 永远不碰：Claude 的长期记忆、凭据
NEVER_PARTS = ["/memory/", "/skills/", "/.git/"]
NEVER_NAMES = ["memory", "skills", "settings.json", "config.toml", ".credentials.yaml", ".credentials.json", ".claude.json",
               "desktop.ini", "pagefile.sys", "hiberfil.sys", "swapfile.sys"]

_A = "%APPDATA%"
_L = "%LOCALAPPDATA%"

# 每条规则：分组、说明、要找的路径（glob，找到的每一项单独移动）、多少天没改过才算没用、默认勾不勾。
# 跟 Mac 版一一对应；Mac 专属的（Chrome code_sign_clone、Sparkle 更新暂存、Homebrew）换成 Windows 上同类的东西。
# skip_running：["-x", "进程名"]（不分大小写，.exe 可写可不写）或 ["-f", "正则"]（对 exe 路径 + 命令行）。
RULES = [
    # ---- Claude ----
    {"id": "claude-cli-cache", "group": "Claude", "label": "Claude Code 缓存和 MCP 日志",
     "paths": [_L + "/claude-cli-nodejs/Cache/*/*"], "days": 7, "on": True},
    {"id": "claude-telemetry", "group": "Claude", "label": "Claude Code 没发出去的统计记录", "paths": ["~/.claude/telemetry/*"], "days": 7, "on": True},
    {"id": "claude-session-env", "group": "Claude", "label": "Claude Code 旧会话留下的空文件夹", "paths": ["~/.claude/session-env/*"], "days": 7, "on": True},
    {"id": "claude-file-history", "group": "Claude", "label": "Claude Code 旧会话的改动备份", "paths": ["~/.claude/file-history/*"], "days": 30, "on": True},
    {"id": "claude-uploads", "group": "Claude", "label": "Claude Code 旧会话里上传的文件", "paths": ["~/.claude/uploads/*"], "days": 30, "on": True},
    {"id": "claude-orphans", "group": "Claude", "label": "文件夹已经不在的 Claude Code 会话记录", "paths": ["~/.claude/projects/*"], "days": 7, "on": True, "orphan": True},
    {"id": "claude-baks", "group": "Claude", "label": "Claude 旧设置备份", "paths": ["~/.claude/*.bak*", "~/.claude/*backup", "~/.claude/backups/*"], "days": 30, "on": True},
    {"id": "claude-app-logs", "group": "Claude", "label": "Claude 桌面版旧日志", "paths": [_A + "/Claude/logs/*"], "days": 14, "on": True},
    {"id": "claude-old-versions", "group": "Claude", "label": "Claude 桌面版里旧版本的 Claude Code", "paths": [_A + "/Claude/claude-code/*"],
     "days": 0, "on": True, "keep_newest": 1},
    # Claude 桌面版和 Claude Code 在 Windows 上进程名都是 claude.exe：任何一个开着都不清，宁可少清
    {"id": "claude-app-cache", "group": "Claude", "label": "Claude 桌面版网页缓存（只在 Claude 没打开时清）",
     "paths": [_A + "/Claude/Cache", _A + "/Claude/Code Cache", _A + "/Claude/GPUCache", _A + "/Claude/*DawnCache",
               _A + "/Claude/Dawn*Cache"],
     "days": 0, "on": True, "skip_running": ["-x", "claude"]},
    {"id": "claude-tmp", "group": "Claude", "label": "Claude 测试用临时目录", "paths": ["%TEMP%/claude*"], "days": 2, "on": True},
    {"id": "claude-sessions", "group": "Claude", "label": "60 天前的 Claude Code 会话记录（清了就不能再接着那次聊）",
     "paths": ["~/.claude/projects/*/*.jsonl"], "days": 60, "on": False},
    # ---- Codex ----
    {"id": "codex-tmp", "group": "Codex", "label": "Codex 临时文件", "paths": ["~/.codex/tmp/*", "%TEMP%/codex*", "%TEMP%/.com.openai.codex.*"],
     "days": 7, "on": True, "skip_running": ["-x", "codex"]},
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
     "paths": ["%TEMP%/playwright*", "%TEMP%/pytest-of-*"], "days": 2, "on": True},
    {"id": "test-leftovers", "group": "测试残留", "label": "项目里的旧测试产物（临时目录、测试报告、.bak 备份）", "find": TEST_NAMES, "days": 14, "on": True},
    {"id": "test-logs", "group": "测试残留", "label": "项目里 30 天没动过的大日志（超过 1 MB）", "find": ["*.log"], "min_bytes": 1048576, "days": 30, "on": True},
    {"id": "test-dirs", "group": "测试残留", "label": "名字带「测试」的旧文件夹（请先看一眼）", "find": TEST_DIRS_CN, "dirs_only": True, "days": 7, "on": False, "any_dir": True},
    # ---- 旧安装包（每晚由 DeepSeek 再核对一遍）----
    {"id": "installers", "group": "安装包", "label": "下载里 30 天前的安装包（.exe / .msi）",
     "paths": ["~/Downloads/*.exe", "~/Downloads/*.msi", "~/Downloads/*.msix", "~/Downloads/*.msixbundle", "~/Downloads/*.appx",
               "~/Desktop/*.msi"], "days": 30, "on": True},
    # Electron 应用（electron-updater）下载好的新版本安装包，装完就没用了；Squirrel 安装器的临时目录
    {"id": "updater-staging", "group": "安装包", "label": "应用自动更新留下的旧安装文件",
     "paths": [_L + "/*-updater/pending/*", _L + "/SquirrelTemp/*"], "days": 1, "on": True},
    # ---- 系统数据 ----
    # %TEMP% 里一周没动过的东西。正在用的（有程序打开着）跳过；每晚由 DeepSeek 逐项核对，不是 safe
    {"id": "win-temp", "group": "系统数据", "label": "临时文件夹里一周没动过的东西（正在用的不动）",
     "paths": ["%TEMP%/*"], "days": 7, "on": True, "skip_in_use": True, "skip_names": ["Low", "_MEI*", "*.lock"]},
    # Codex 插件市场升级的暂存克隆。用户：Codex 这里要先问他，所以默认不勾，只在卡片上提醒
    {"id": "codex-staging", "group": "系统数据", "label": "Codex 插件市场升级留下的暂存克隆（先问用户）",
     "paths": ["~/.codex/.tmp/marketplaces/.staging/marketplace-*"], "days": 0, "on": False},
    # ---- 缓存（用到会自动重新生成；跟工具箱「垃圾清理」共用这份清单）----
    {"id": "dev-caches", "group": "缓存", "label": "开发工具的下载缓存（uv、pip、npm、pnpm、Yarn 等，用到会自动重新下载）",
     "paths": [_L + "/uv/cache", "~/.cache/uv", _L + "/pnpm/store", _L + "/npm-cache/_cacache", _L + "/pip/Cache",
               _L + "/Yarn/Cache", _L + "/electron/Cache", _L + "/electron-builder/Cache", _L + "/node-gyp/Cache",
               _L + "/ms-playwright-mcp"], "days": 0, "on": True, "safe": True},
    {"id": "updater-caches", "group": "缓存", "label": "应用更新器的缓存（OpenCode、DeepSeek、Notion）",
     "paths": [_L + "/@opencodedesktop-updater", _L + "/@deepseek-aidsh-desktop-updater", _L + "/notion-updater"],
     "days": 0, "on": True, "safe": True},
    {"id": "chrome-cache", "group": "缓存", "label": "Chrome 网页缓存（只在 Chrome 没打开时清）",
     "paths": [_L + "/Google/Chrome/User Data/*/Cache", _L + "/Google/Chrome/User Data/*/Code Cache",
               _L + "/Google/Chrome/User Data/*/GPUCache", _L + "/Google/Chrome/User Data/GrShaderCache",
               _L + "/Google/Chrome/User Data/ShaderCache"],
     "days": 0, "on": True, "safe": True, "skip_running": ["-x", "chrome"]},
    # Edge 常驻后台（启动加速），多半会被跳过；真关掉了才清
    {"id": "edge-cache", "group": "缓存", "label": "Edge 网页缓存（只在 Edge 完全关掉时清）",
     "paths": [_L + "/Microsoft/Edge/User Data/*/Cache", _L + "/Microsoft/Edge/User Data/*/Code Cache",
               _L + "/Microsoft/Edge/User Data/*/GPUCache", _L + "/Microsoft/Edge/User Data/GrShaderCache",
               _L + "/Microsoft/Edge/User Data/ShaderCache"],
     "days": 0, "on": True, "safe": True, "skip_running": ["-x", "msedge"]},
]


def _x(p):
    """展开 ~、%TEMP%、%APPDATA% 等。环境变量没有（比如在 Linux 上）就返回 None，免得按字面去找。"""
    p = p.replace("$TMPDIR", TEMP).replace("%TEMP%", TEMP)
    p = os.path.expandvars(os.path.expanduser(p))
    if "%" in p:
        return None
    return os.path.normpath(p)


def _slash(p):
    return p.replace("\\", "/")


def _norm(p):
    return os.path.normcase(os.path.abspath(p))


def _home(p):
    if p and _norm(p).startswith(_norm(HOME)):
        return "~" + p[len(HOME):]
    return p


_ROOTS = []


def _roots():
    """移动了会出大事的文件夹本身（只能清它们里面的东西）。算一次记下来。"""
    if _ROOTS:
        return _ROOTS[0]
    out = {HOME, TEMP, tempfile.gettempdir(), os.path.join(HOME, "Downloads"), os.path.join(HOME, "Desktop"),
           os.path.join(HOME, "Documents")}
    for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "SystemRoot", "ProgramFiles", "ProgramData"):
        if os.environ.get(k):
            out.add(os.environ[k])
    drive = os.path.splitdrive(HOME)[0]
    if drive:
        out.add(drive + os.sep)
    out.add(os.sep)
    _ROOTS.append({_norm(os.path.realpath(p)) for p in out if p})
    return _ROOTS[0]


def _protected(p):
    name = os.path.basename(p.rstrip("\\/"))
    s = _slash(p)
    if any(k in s for k in PROTECT) or any(k in s + "/" for k in NEVER_PARTS) or name.lower() in NEVER_NAMES or "记录" in name:
        return True
    mei = getattr(sys, "_MEIPASS", None)  # 打包后的 Deck 自己解压在 %TEMP%\_MEIxxxx，绝不能动
    rp = _norm(os.path.realpath(p))
    if mei and (rp == _norm(mei) or rp.startswith(_norm(mei) + os.sep)):
        return True
    return rp in _roots()


def _is_link(p):
    """软链接和目录联接（junction）都不跟进、不移动。"""
    try:
        return os.path.islink(p) or bool(getattr(os.path, "isjunction", lambda _: False)(p))
    except OSError:
        return True


def _stat_tree(p, limit=20000):
    """(总字节, 最近修改时间, 文件数)。不跟随软链接和联接；文件太多时只看前 limit 个。"""
    try:
        st = os.lstat(p)
    except OSError:
        return 0, 0, 0
    if not os.path.isdir(p) or _is_link(p):
        return st.st_size, st.st_mtime, 1
    size, newest, n = 0, 0, 0  # 文件夹看里面文件的最近修改时间；空文件夹才看它自己的
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if not _is_link(os.path.join(root, d))]
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


_NOWIN = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW：调 git 时不闪黑框


def _git_tracked(p):
    """在不在 git 里。电脑上没装 git 时，只要往上有 .git 就当作在（宁可少清）。"""
    d = p if os.path.isdir(p) else os.path.dirname(p)
    if not shutil.which("git"):
        cur = d
        while True:
            if os.path.exists(os.path.join(cur, ".git")):
                return True
            up = os.path.dirname(cur)
            if up == cur:
                return False
            cur = up
    try:
        r = subprocess.run(["git", "-C", d, "ls-files", "--error-unmatch", p], capture_output=True, timeout=5,
                           creationflags=_NOWIN)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


SKIP_DIRS = ("node_modules", ".git", ".venv", "venv", "AppData", "Library")


def _find(patterns, dirs_only=False):
    out = []
    for root in PROJECT_ROOTS:
        root = _x(root)
        if not root or not os.path.isdir(root):
            continue
        for cur, dirs, files in os.walk(root):
            if _protected(cur):
                dirs[:] = []
                continue
            depth = len(os.path.relpath(cur, root).replace("\\", "/").split("/")) if cur != root else 0
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".app") and not _is_link(os.path.join(cur, d))]
            for name in list(dirs) + ([] if dirs_only else files):
                if any(fnmatch.fnmatch(name, pat) for pat in patterns):
                    out.append(os.path.join(cur, name))
                    if name in dirs:
                        dirs.remove(name)
            if depth >= 5:
                dirs[:] = []
    return out


SKIP_PARTS = ["/profile-archive/"]  # 有意留的配置快照，拿掉 .bak 会让快照不完整
MARKERS = (".git", "package.json", "pyproject.toml", "Package.swift", "Cargo.toml", "go.mod", "Makefile", "requirements.txt",
           "*.sln", "*.csproj")


def _has_marker(d):
    for m in MARKERS:
        if ("*" in m and glob.glob(os.path.join(d, m))) or ("*" not in m and os.path.exists(os.path.join(d, m))):
            return True
    return False


def _in_project(p):
    """find 规则只清开发项目里的测试残留：往上找到项目标记（.git、package.json…）才算；课程资料等普通文件夹里的 tmp 不碰。"""
    s = _slash(p)
    if any(x in s + "/" for x in SKIP_PARTS):
        return False
    if "/.pnpm-store/" in s:
        return True
    d = os.path.dirname(p)
    roots = {_norm(r) for r in (_x(r) for r in PROJECT_ROOTS) if r}
    while _norm(d) not in roots and _norm(d) != _norm(HOME):
        if _has_marker(d):
            return True
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
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
                    if '"cwd"' not in line:
                        continue
                    try:
                        cwd = json.loads(line).get("cwd")
                    except (ValueError, AttributeError):
                        m = re.search(r'"cwd"\s*:\s*("(?:[^"\\]|\\.)*")', line)
                        cwd = json.loads(m.group(1)) if m else None
                    if isinstance(cwd, str) and cwd:
                        return not os.path.exists(cwd)
        except (OSError, ValueError):
            continue
    return False


def _procs(attrs):
    import psutil
    return psutil.process_iter(attrs)


def _base(name):
    n = (name or "").lower()
    return n[:-4] if n.endswith(".exe") else n


def _running(args):
    """args 例如 ["-x", "claude"]（按进程名，不分大小写，不管 .exe）或 ["-f", "正则"]（按 exe 路径 + 完整命令行）。
    出错就当作在运行，不清。"""
    try:
        mode, pat = args[0], args[1]
        rx = re.compile(pat, re.I) if mode == "-f" else None
        me = os.getpid()
        for p in _procs(["pid", "name", "exe", "cmdline"]):
            info = p.info
            if info.get("pid") == me:
                continue
            if mode == "-x":
                if _base(info.get("name")) == _base(pat):
                    return True
            elif rx.search((info.get("exe") or "") + " " + " ".join(info.get("cmdline") or [])):
                return True
        return False
    except Exception:  # noqa: BLE001
        return True


def _in_use(paths):
    """哪些路径（文件或目录）底下有正在运行的程序或程序的当前目录。
    用 psutil 看每个进程；psutil 用不了就当全部在用，不清。别人的系统进程看不到（没权限）没关系：
    真被占着的东西 Windows 也不让移，会记成「没移动成功」，下次再试。"""
    if not paths:
        return set()
    try:
        procs = list(_procs(["pid", "exe", "cwd"]))
    except Exception:  # noqa: BLE001
        return set(paths)
    # 不用 p.open_files()：Windows 上它要挨个查每个进程的每个句柄，几十秒到几分钟，而且一直占着 GIL，
    # 整个后台（网页、Vesper）都会卡死（11-08 CI 实测）。被打开着的文件 Windows 本来就不让移（同盘移进回收站是一次改名，
    # 文件夹里有一个文件被占着整个都移不动，不会只移走一半），会记成「没移动成功」下次再试。
    opened = []
    for p in procs:
        info = p.info
        opened += [x for x in (info.get("exe"), info.get("cwd")) if x]
    opened = [_norm(o) for o in opened]
    busy = set()
    for p in paths:
        rp = _norm(os.path.realpath(p))
        if any(o == rp or o.startswith(rp + os.sep) for o in opened):
            busy.add(p)
    return busy


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
                xp = _x(pat)
                if xp:
                    cands += glob.glob(xp)
        cands = sorted({os.path.normpath(c) for c in cands})
        if r.get("skip_names"):
            cands = [p for p in cands if not any(fnmatch.fnmatch(os.path.basename(p), n) for n in r["skip_names"])]
        if r.get("skip_in_use"):
            busy = _in_use(cands)
            cands = [p for p in cands if p not in busy]
        if r.get("keep_newest"):
            keep = sorted(cands, key=lambda p: os.lstat(p).st_mtime if os.path.lexists(p) else 0)[-r["keep_newest"]:]
            cands = [p for p in cands if p not in keep]
        items = []
        for p in cands:
            rp = _norm(os.path.realpath(p))
            if rp in seen or _protected(p) or _is_link(p):
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


TRASH_LOCK = threading.Lock()  # 移进去和放回来不同时做


def move_to_trash(src, check=None):
    """把 src 移进回收站，返回它在回收站里的路径（撤销用）。check(src) 返回原因字符串时拒绝。
    没有回收站的盘（U 盘、网络盘）不动，绝不直接删除。返回空字符串表示进了回收站、但 Deck 找不到位置（只能在资源管理器里还原）。"""
    with TRASH_LOCK:
        return common.recycle(src, check)


def _trash_target(p):
    """Mac 版用来给废纸篓里的新名字。Windows 上不能自己往回收站里放东西（系统要记索引），一律走 move_to_trash。"""
    raise OSError(1, "Windows 上请用 move_to_trash 移进回收站")


def _log(lines):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), ln))
    except OSError:
        pass


def run(plan, rule_ids, rules=None, log_title="开始清理（Vesper，你确认后执行）", tag="Vesper 清理", check=None):
    """把选中规则里的东西移到回收站（只移动，不删除）。返回 {moved:[{src,dst,bytes}], failed:[path], bytes}。"""
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
                # 「移到废纸篓：」是日志格式，每日清理卡片（monitor/cleanup.py）按这几个字解析，两个系统共用，不改
                logs.append("移到废纸篓：%s -> %s (%dM)" % (src, dst or "回收站", round(it["bytes"] / 1048576)))
            except OSError as e:
                failed.append(src)
                logs.append("失败：%s（%s）" % (src, e.strerror or e))
    total = sum(m["bytes"] for m in moved)
    logs.append("完成：移走 %d 项，共 %dM；%s" % (len(moved), round(total / 1048576), tag))
    _log(logs)
    return {"moved": moved, "failed": failed, "bytes": total}


def undo(moved):
    """把刚移进回收站的东西放回原处（原位置已有同名东西、或回收站已清空就跳过）。"""
    back, skipped = 0, []
    for m in moved:
        if m.get("dst") and os.path.lexists(m["dst"]) and not os.path.lexists(m["src"]):
            try:
                with TRASH_LOCK:
                    common.restore(m["dst"], m["src"])
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
