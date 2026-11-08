"""检查 Windows 上 API key 从凭据管理器读（Mac 钥匙串的 Windows 版）。直接运行：python windows/ci/test_keys.py
用 cmdkey 存一条测试凭据（假 key），读出来对得上，再删掉这条测试凭据（只删自己建的那条）。"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "manager"))
from winport import common  # noqa: E402

bad = 0


def check(ok, what):
    global bad
    print(("通过  " if ok else "失败  ") + what)
    bad += 0 if ok else 1


T, FAKE = "deck-ci-test-key", "sk-ci-0123456789abcdef"
check(common._cred_text("sk-abc".encode("utf-16-le")) == "sk-abc", "UTF-16 的凭据读得对")
check(common._cred_text(b"sk-abcd12") == "sk-abcd12", "UTF-8 的凭据（偶数长度）不会被当成 UTF-16")
if sys.platform == "win32":
    check(common.cred_read(T) is None, "没有这条凭据时返回 None")
    r = subprocess.run(["cmdkey", "/generic:" + T, "/user:deck", "/pass:" + FAKE], capture_output=True, text=True)
    check(r.returncode == 0, "cmdkey 存测试凭据")
    try:
        check(common.cred_read(T) == FAKE, "从凭据管理器读出来的 key 对得上")
    finally:
        subprocess.run(["cmdkey", "/delete:" + T], capture_output=True)
    check(common.cred_read(T) is None, "测试凭据已清掉")
else:
    print("跳过  cmdkey（只在 Windows 上测）")
print("全部通过" if not bad else "%d 项失败" % bad)
sys.exit(1 if bad else 0)
