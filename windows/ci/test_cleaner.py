"""Windows 版清理的检查（11-08 第二步）。直接运行：python windows/ci/test_cleaner.py，失败时退出码不为 0。

在 Windows 上：临时文件夹里造一个假缓存 → 扫描 → 移进回收站（确认原位置没了、回收站里有）→ 撤销（确认放回、内容不变）。
不在 Windows 上（Linux 上 DECK_OS=win）：只检查能导入、扫描和每日清理的数据结构，并确认移动失败时文件原样留着。
不碰真实规则里的任何文件：移动只对测试自己造的文件做。
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "manager"))
os.environ.setdefault("DECK_OS", "win")

from winport import cleaner, common, daily_clean  # noqa: E402

IS_WIN = sys.platform == "win32"
FAILS = []


def check(ok, what):
    print(("通过  " if ok else "失败  ") + what)
    if not ok:
        FAILS.append(what)


def plan_shape(plan):
    return set(plan) >= {"id", "at", "rules", "bytes"} and all(
        set(r) >= {"id", "group", "label", "on", "days", "safe", "count", "bytes", "examples", "items"} for r in plan["rules"])


def bootstrap_bin(path):
    """全新的 CI 机器上这个盘可能还没有当前用户的回收站文件夹：先让系统回收一个测试自己造的空文件，它就会建好。"""
    d = common.bin_dir(path)
    if d and os.path.isdir(d):
        return
    seed = os.path.join(os.path.dirname(path), "deck-bin-seed.txt")
    with open(seed, "w") as f:
        f.write("seed")
    import ctypes
    op = common._SHFILEOPSTRUCTW(None, common.FO_DELETE, seed + "\x00\x00", None,
                                 common.FOF_ALLOWUNDO | common.FOF_NOCONFIRMATION | common.FOF_SILENT | common.FOF_NOERRORUI, 0, None, None)
    ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))


def test_cleaner():
    base = tempfile.mkdtemp(prefix="deck-clean-test-")
    real_log = cleaner.LOG
    cleaner.LOG = os.path.join(base, "clean.log")  # 测试的记录不写进真的清理日志（卡片会读它）
    try:
        cache = os.path.join(base, "fakecache", "Cache")
        os.makedirs(cache)
        data = os.path.join(cache, "data_1")
        with open(data, "w", encoding="utf-8") as f:
            f.write("假缓存内容")
        old = time.time() - 10 * 86400
        os.utime(data, (old, old))
        rule = {"id": "test-cache", "group": "缓存", "label": "测试用假缓存", "paths": [base.replace("\\", "/") + "/fakecache/*"],
                "days": 7, "on": True, "safe": True}
        rules = [rule]

        plan = cleaner.scan(rules)
        check(plan_shape(plan), "scan 返回的结构跟 Mac 版一样")
        check(len(plan["rules"]) == 1 and plan["rules"][0]["count"] == 1, "扫到测试缓存")
        pub, summ = cleaner.public(plan), cleaner.summary(plan)
        check(set(pub) == {"id", "at", "bytes", "rules"} and "examples" in pub["rules"][0], "public 结构")
        check(set(summ) == {"id", "total_mb", "rules"} and "default_on" in summ["rules"][0], "summary 结构")

        # 保护：家目录、记忆、凭据永远不动
        check(cleaner._protected(os.path.expanduser("~")), "家目录受保护")
        check(cleaner._protected(os.path.join(base, "memory", "x")) or cleaner._protected(os.path.join(base, "memory")), "memory 受保护")
        check(cleaner._protected(os.path.join(base, ".credentials.json")), "凭据文件受保护")
        check(not cleaner._protected(cache), "普通缓存不受保护")
        check(cleaner.human(1536) == "1 KB" and cleaner.human(5 * 1024 ** 3) == "5.0 GB", "human 格式")

        if not plan["rules"]:
            return
        src = plan["rules"][0]["items"][0]["path"]
        if IS_WIN:
            bootstrap_bin(src)
        res = cleaner.run(plan, {"test-cache"}, rules, log_title="开始清理（CI 检查）", tag="CI 检查")
        check(set(res) == {"moved", "failed", "bytes"}, "run 返回的结构")
        if not IS_WIN:
            check(not res["moved"] and res["failed"] == [src], "不在 Windows 上时不移动，记成失败")
            check(os.path.exists(data), "移动失败时文件原样留着（绝不直接删除）")
            return
        check(len(res["moved"]) == 1, "移进了回收站")
        if not res["moved"]:  # 打出原因，方便看 CI 日志
            try:
                with open(cleaner.LOG, encoding="utf-8") as f:
                    print("清理日志：\n" + f.read())
            except OSError:
                pass
            print("盘：%r 回收站：%r 存在：%r 设置：%r" % (os.path.splitdrive(src)[0], common.bin_dir(src),
                  os.path.isdir(common.bin_dir(src) or ""), common.bin_limit(src)))
        dst = res["moved"][0]["dst"] if res["moved"] else ""
        check(not os.path.exists(src), "原位置没了")
        bd = common.bin_dir(src)
        check(bool(dst) and os.path.exists(dst) and bd and os.path.normcase(dst).startswith(os.path.normcase(bd)),
              "在回收站文件夹里找得到（%s）" % dst)
        back = cleaner.undo(res["moved"])
        check(back == {"restored": 1, "skipped": []}, "撤销放回 1 项")
        ok = os.path.exists(data)
        if ok:
            with open(data, encoding="utf-8") as f:
                ok = f.read() == "假缓存内容"
        check(ok, "放回原位置，内容不变")
        check(not os.path.exists(dst), "回收站里不再留着")
        with open(cleaner.LOG, encoding="utf-8") as f:
            log = f.read()
        check("移到废纸篓：" in log and "撤销 Vesper 清理：放回 1 项" in log, "日志格式和卡片解析的一致")
    finally:
        cleaner.LOG = real_log
        shutil.rmtree(base, ignore_errors=True)  # 测试自己造的临时文件夹


def test_real_scan():
    plan = cleaner.scan()  # 只读：只看大小和时间，不动文件
    check(plan_shape(plan), "真实规则扫描不出错，结构正确（%d 条规则有东西）" % len(plan["rules"]))
    ids = [r["id"] for r in cleaner.RULES]
    check(len(ids) == len(set(ids)), "规则 id 不重复")
    check(all(set(r) >= {"id", "group", "label", "days", "on"} and (r.get("paths") or r.get("find")) for r in cleaner.RULES), "规则字段齐全")
    check(all(r.get("safe") for r in cleaner.RULES if r["group"] == "缓存"), "缓存组都是 safe")


def test_daily():
    st = daily_clean.state()
    check(isinstance(st, dict) and isinstance(st.get("auto"), bool) and isinstance(st.get("next_at"), float), "state() 结构")
    na = daily_clean.next_at()
    check(time.time() < na <= time.time() + 86400 + 60, "next_at() 在接下来 24 小时内")
    s = daily_clean.sample()
    check(set(s) == {"at", "free", "temp", "winupdate", "codex"} and s["free"] > 0, "sample() 结构（可用 %.0fGB）" % (s["free"] / 1024 ** 3))
    check(daily_clean.surge([]) == ([], False), "surge 空记录")
    G = 1024 ** 3
    now = time.time()
    why, warn = daily_clean.surge([{"at": now - 1800, "free": 100 * G}, {"at": now, "free": 80 * G, "temp": 11 * G, "codex": 4 * G}], now)
    check(len(why) == 2 and warn, "surge 判断：掉了 20GB、临时文件夹 11GB、Codex 要提醒")
    why, warn = daily_clean.surge([{"at": now, "free": 10 * G}], now)
    check(why and why[0].startswith("可用空间只剩") and not warn, "surge 判断：可用空间低")
    b = daily_clean._brief({"rules": [{"on": True, "group": "缓存", "label": "x", "bytes": 5, "count": 1}]})
    check(set(b) == {"bytes", "count", "top", "cats"} and b["cats"][0]["name"] == "下载缓存", "_brief 结构")
    check(daily_clean._night(__import__("datetime").datetime(2026, 11, 8, 23, 45)) == "2026-11-08", "_night 23:45 算今晚")


if __name__ == "__main__":
    print("系统：%s（%s）" % (sys.platform, "完整检查" if IS_WIN else "跳过只有 Windows 才能做的检查"))
    for t in (test_cleaner, test_real_scan, test_daily):
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(False, "%s 出错：%s" % (t.__name__, e))
    print("全部通过" if not FAILS else "%d 项失败" % len(FAILS))
    sys.exit(1 if FAILS else 0)
