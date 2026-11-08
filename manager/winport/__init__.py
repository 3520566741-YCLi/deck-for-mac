"""Windows 版的系统层（11-08 第二步）。

Mac 版的 procs / diskwatch / cleaner / daily_clean / apptools / kbclean / files 用的是 ps、top、ioreg、mdfind、du、
废纸篓、Swift 小程序；这里每个模块写一份 Windows 版，**函数名和返回的数据结构跟 Mac 版一模一样**，页面和 Vesper 不用改。

server.py 在 Windows 上最先调用 install()：把这些模块登记成同名模块（sys.modules["procs"] = winport.procs …），
之后各处照常 `import procs` 拿到的就是 Windows 版。Mac 上不调用，什么都不变。
"""
import importlib
import sys

# 同名模块 → winport 里的文件。写好一个加一个
MODULES = {
    "procs": "winport.procs",
    "diskwatch": "winport.diskwatch",
    "cleaner": "winport.cleaner",
    "daily_clean": "winport.daily_clean",
    "apptools": "winport.apptools",
    "kbclean": "winport.kbclean",
    "files": "winport.files",
}


def install():
    """把已经写好的 Windows 模块登记成同名模块。缺的（还没写或导入出错）跳过，Mac 版那个也不会被导入。"""
    done = []
    for name, target in MODULES.items():
        if name in sys.modules and getattr(sys.modules[name], "__winport__", False):
            done.append(name)
            continue
        try:
            mod = importlib.import_module(target)
        except ImportError as e:
            if getattr(e, "name", None) == target:  # 还没写这个文件
                continue
            raise
        mod.__winport__ = True
        sys.modules[name] = mod
        done.append(name)
    return done
