# PyInstaller 打包配置：Deck for Windows（11-08）
# 用法（在 Windows 上，仓库根目录）：
#   pip install -r windows/requirements-build.txt
#   pyinstaller windows/deck.spec --noconfirm
# 产物：dist/Deck/Deck.exe（文件夹形式，启动快、杀毒误报少）；再用 windows/installer.iss 做成安装包。
#
# manager/ 整个以「源文件」形式放进包里（server.py 用 __file__ 找 dashboard.html 等文件，必须保留目录结构），
# 它用到的标准库和第三方模块在这里扫一遍，作为 hiddenimports 让 PyInstaller 一起打进去。
import ast
import importlib.util
import os

from PIL import Image
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
MANAGER = os.path.join(ROOT, "manager")
# Mac 专属、Windows 版用不到的目录：Swift 小程序和 iPad 音箱工程
SKIP_DIRS = {"__pycache__", "hands", "ipad-speaker", "helper", "models", "venv", ".venv"}
SKIP_FILES = {"config.json", "pair.key", "check-history.json", "server.log"}  # 本机私有，绝不打进安装包
# 语音（11-08 第三步）：manager/voice/service 也扫进来（它的调试脚本目录 tools/ 不扫）；这些只有 Mac 有或只在 Mac 上用，不打包
MAC_ONLY = {"mlx", "mlx_audio", "huggingface_hub", "resource", "AppKit", "Foundation", "objc"}
# 带 DLL / 数据文件的第三方包：整包收进来（sherpa_onnx/lib 下的 onnxruntime.dll、sounddevice 的 PortAudio、opencc 词库）
VOICE_PKGS = ["sherpa_onnx", "numpy", "sounddevice", "pypinyin", "opencc"]


def manager_files():
    out = []
    for d, dirs, files in os.walk(MANAGER):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            if f in SKIP_FILES or f.endswith((".pyc", ".swift", ".entitlements")):
                continue
            src = os.path.join(d, f)
            rel = os.path.relpath(d, ROOT)
            out.append((src, rel))
    return out


def manager_imports():
    """manager 里 import 了哪些不在 manager 里的模块（标准库 + 第三方）。"""
    local = set()
    for d, dirs, files in os.walk(MANAGER):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        local.update(f[:-3] for f in files if f.endswith(".py"))
        local.update(dirs)
    names = set()
    for d, dirs, files in os.walk(MANAGER):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS | {"tools"}]
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                tree = ast.parse(open(os.path.join(d, f), encoding="utf-8").read())
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    names.update(a.name for a in n.names)
                elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                    names.add(n.module)
    keep = []
    for name in sorted(names):
        top = name.split(".")[0]
        if top in local or top in MAC_ONLY:
            continue
        try:
            if importlib.util.find_spec(top) is None:
                continue
        except (ImportError, ValueError):
            continue
        keep.append(name)
    return keep


# 托盘 / 窗口图标：从网页图标生成多尺寸 .ico
ICO = os.path.join(SPECPATH, "build", "deck.ico")
os.makedirs(os.path.dirname(ICO), exist_ok=True)
Image.open(os.path.join(MANAGER, "static", "dash-icon.png")).convert("RGBA").save(
    ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

hidden = manager_imports() + ["psutil", "winreg", "sqlite3", "webview", "pystray", "PIL.Image"]
voice_datas, voice_bins = [], []
for pkg in VOICE_PKGS:
    try:
        d, b, h = collect_all(pkg)
    except Exception as e:  # noqa: BLE001  没装这个包就打不进语音，打包照样继续（语音服务会提示缺依赖）
        print("[deck.spec] 语音依赖 %s 没装：%s" % (pkg, e))
        continue
    voice_datas += d
    voice_bins += b
    hidden += h
print("[deck.spec] hiddenimports:", ", ".join(hidden))

a = Analysis(
    [os.path.join(SPECPATH, "deck_win.py")],
    pathex=[],
    datas=manager_files() + [(os.path.join(SPECPATH, "VERSION"), "windows")] + voice_datas,  # VERSION：管家页「Deck 自己」显示版本号
    binaries=voice_bins,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "pytest"] + sorted(MAC_ONLY - {"resource"}),  # numpy 语音要用，不再排除
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Deck",
    icon=ICO,
    console=False,          # 窗口程序，不弹黑框；输出写到 %LOCALAPPDATA%\Deck\logs
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Deck")
