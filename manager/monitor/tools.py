"""工具箱卡片：卸载应用、垃圾清理、文件管理、应用更新的摘要。
只读缓存（上次检查更新的结果、上次清理）和磁盘剩余空间，不做耗时操作、不动文件。"""
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import apptools  # noqa: E402


class ToolsSource:
    key = "tools"
    title = "工具箱"
    default_interval = 300

    def __init__(self, cfg):
        self.cfg = cfg
        self.trash, self.trash_at = None, 0

    def collect(self, force=False):
        s = apptools.summary()
        try:
            du = shutil.disk_usage(os.path.expanduser("~"))
            s["disk_free"], s["disk_total"] = du.free, du.total
        except OSError:
            s["disk_free"] = s["disk_total"] = None
        if time.time() - self.trash_at > 1800:  # 废纸篓可能很大，du 半小时量一次
            self.trash, self.trash_at = apptools.trash_size(), time.time()
        s["trash_bytes"] = self.trash
        old = s.get("updates_at")
        stale = not old or time.time() - old > 7 * 86400
        s["level"] = "warn" if s.get("disk_free") is not None and s["disk_free"] < 20 * 2 ** 30 else "ok"
        why = []
        if s["level"] == "warn":
            why.append({"text": "磁盘只剩 %s" % apptools.human(s["disk_free"]), "fix": "打开工具箱 → 垃圾清理"})
        if stale:
            why.append({"text": "超过一周没检查应用更新", "fix": "打开工具箱 → 更新"})
        s["why"] = why
        return s
