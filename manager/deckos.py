"""Deck 跑在哪个系统上（11-08 Windows 版）。

WIN 为真时：系统状态用 psutil 版、只开跨平台的卡、页面固定新拟态并藏起 Mac 专属设置。
开发 / CI 可以设环境变量 DECK_OS=win，在 Linux 上模拟 Windows 版（Linux 默认仍按 Mac 版跑，不影响现有检查）。
"""
import os
import sys

WIN = sys.platform == "win32" or os.environ.get("DECK_OS", "").lower() == "win"
MAC = sys.platform == "darwin" and not WIN
NAME = "win" if WIN else "mac"

# Windows 版还没换成 Windows 写法的功能（读的是 Mac 的目录、ps / mdfind / Swift 小程序）。
# 在 Windows 上：后台接口直接回「还没做」，Vesper 也拿不到这些工具，绝不会在 Windows 上乱清、乱删、乱点。
# 第二步起每接好一个，就从这里删掉一个。
# 11-08 第二步已接好：管家页（/api/action、/api/deep）、进程、磁盘面板、清理 / 每日清理、找文件、工具箱。
# 11-08 第三步已接好：电脑操控（screen_* / app_control，winport/hands_win.py + appctl.py）、显示方式（顶部面板）。
# 语音（windows/voice_*.py：唤醒、识别、朗读、换语速）也已接好。
# 还没做的：iPad 有线音效、局域网开关（Windows 上不做平板扩展屏）。
WIN_TODO_API = ("/api/sfx", "/api/dash/lan")
WIN_TODO_TOOLS = set()
WIN_TODO_MSG = "Windows 版还没做这个功能，后面几步会接上。"


def support(*parts):
    """应用数据放哪：Mac 是 ~/Library/Application Support/<parts>，Windows 是 %APPDATA%\\<parts>（漫游配置目录）。
    Mac 上和原来写死的路径完全一样，老数据不用搬。"""
    if WIN and sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    else:
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    return os.path.join(base, *parts)


# Windows 上给 Vesper 和卡片看的文字：Mac 的说法换成 Windows 的（网页上的换法在 static/deck-i18n.js 的 osFix）
_WIN_WORDS = (("清空废纸篓", "清空回收站"), ("废纸篓", "回收站"), ("在访达中显示", "在资源管理器中显示"), ("访达", "资源管理器"),
              ("程序坞", "任务栏"), ("LaunchAgent", "计划任务"), ("钥匙串", "凭据管理器"), ("这台 Mac", "这台电脑"),
              ("Mac 私人控制台", "Windows 电脑上的私人控制台"), ("Spotlight", "Windows 搜索"), ("活动监视器", "任务管理器"),
              # 显示方式（第三步）：Mac 的扩展屏 / 刘海 → Windows 的窗口 / 顶部面板。长的放前面
              ("auto=插着 iPad 用扩展屏、拔掉从刘海弹出；ext=一直在扩展屏；notch=从刘海弹出",
               "auto=接着第二块屏时铺在那块屏上、没接就用顶部面板；ext=普通窗口；notch=顶部面板（从屏幕顶上弹出）"),
              ("自动 / 扩展屏 / 刘海", "自动 / 窗口 / 顶部面板"), ("刘海里卡片怎么排", "顶部面板里卡片怎么排"),
              ("刘海面板", "顶部面板"), ("刘海排法", "顶部面板排法"),
              # 电脑操控、键盘清洁（第三步）
              ("在 Mac 前台应用里", "在前台应用里"), ("cmd+t、cmd+shift+n", "ctrl+t、ctrl+shift+n"), ("cmd+shift+t", "ctrl+shift+t"),
              ("Safari、微信、Finder", "Edge、微信、资源管理器"), ("同时按住左右两个 ⌘ 2 秒", "按住 Esc 3 秒、按 Ctrl+Alt+Del"),
              ("比如 DeckHands 没有辅助功能权限", "比如设置里的开关没开"))


def words(s):
    """WIN 时把 s 里 Mac 的说法换成 Windows 的；Mac 上原样返回。"""
    if not WIN or not isinstance(s, str):
        return s
    for a, b in _WIN_WORDS:
        s = s.replace(a, b)
    return s
