"""Windows 版公用的系统操作（11-08 第二步）：回收站、在资源管理器里显示、打开文件、当前用户。

回收站：和 Mac 版「移到废纸篓、能放回」一样的规矩——
- 用系统的 SHFileOperationW（FOF_ALLOWUNDO）移进回收站，和在资源管理器里按 Delete 完全一样，资源管理器里能「还原」；
- 移完在本盘 $Recycle.Bin\\<你的 SID> 里找到对应的 $I 记录（里面写着原路径），得到 $R 文件的位置，
  Deck 的「撤销」就是把 $R 改名回原位置（同一个盘内改名，不复制），再把那条 $I 记录拿掉，回收站里不留空壳。
- 绝不直接删除用户文件。
只用标准库 + ctypes。
"""
import ctypes
import glob
import os
import struct
import subprocess
import sys
import threading

IS_WIN = sys.platform == "win32"
_lock = threading.Lock()

if IS_WIN:
    from ctypes import wintypes


# ---------------------------------------------------------------- 当前用户

_sid = [None]


def user_sid():
    """当前用户的 SID 字符串（S-1-5-21-…），回收站按它分文件夹。"""
    if _sid[0] or not IS_WIN:
        return _sid[0]
    _sid[0] = _sid_ctypes() or _sid_whoami()
    return _sid[0]


_api = {}
_api_lock = threading.Lock()


def _dlls():
    """自己的一份 kernel32 / advapi32，签名只在第一次设好。
    不改全局 ctypes.windll 的函数签名：那是全进程共用的，别的线程正拿它调用时改 argtypes 会传坏参数。"""
    if not _api:
        with _api_lock:  # 不用 _lock：回收站拿着 _lock 时会查 user_sid()
            if not _api:
                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                adv = ctypes.WinDLL("advapi32", use_last_error=True)
                # 64 位下句柄是指针宽度：不写 argtypes / restype，ctypes 会按 32 位 int 传，GetCurrentProcess 的 -1 就传坏了
                k32.GetCurrentProcess.restype = wintypes.HANDLE
                k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                k32.OpenProcess.restype = wintypes.HANDLE
                k32.LocalFree.argtypes = [ctypes.c_void_p]
                k32.CloseHandle.argtypes = [wintypes.HANDLE]
                adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
                adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                    ctypes.POINTER(wintypes.DWORD)]
                adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
                adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
                adv.CredReadW.restype = wintypes.BOOL
                adv.CredFree.argtypes = [ctypes.c_void_p]
                adv.CredFree.restype = None
                _api.update(k32=k32, adv=adv)
    return _api["k32"], _api["adv"]


def _sid_ctypes(pid=None):
    """进程（不给 pid 就是自己）的用户 SID 字符串。只读令牌，不查用户名（LookupAccountSid 可能很慢）。"""
    k32, adv = _dlls()
    tok = wintypes.HANDLE()
    if pid is None:
        ph = k32.GetCurrentProcess()
    else:
        ph = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not ph:
            return None
    try:
        ok = adv.OpenProcessToken(ph, 0x0008, ctypes.byref(tok))  # TOKEN_QUERY
    finally:
        if pid is not None:
            k32.CloseHandle(ph)
    if not ok:
        return None
    try:
        need = wintypes.DWORD(0)
        adv.GetTokenInformation(tok, 1, None, 0, ctypes.byref(need))  # TokenUser
        if not need.value:
            return None
        buf = ctypes.create_string_buffer(need.value)
        if not adv.GetTokenInformation(tok, 1, buf, need, ctypes.byref(need)):
            return None
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        s = wintypes.LPWSTR()
        if not adv.ConvertSidToStringSidW(psid, ctypes.byref(s)):
            return None
        val = s.value
        k32.LocalFree(ctypes.cast(s, ctypes.c_void_p))
        return val
    except (OSError, ValueError):
        return None
    finally:
        k32.CloseHandle(tok)


def process_sid(pid):
    """别的进程是哪个用户的（SID 字符串）；读不到（系统进程、权限不够）返回 None。"""
    if not IS_WIN:
        return None
    try:
        return _sid_ctypes(pid)
    except (OSError, ValueError, AttributeError):
        return None


def _sid_whoami():
    """兜底：whoami /user（输出格式和系统语言无关）。"""
    try:
        out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, timeout=10,  # noqa: S603,S607
                             creationflags=0x08000000).stdout.decode("mbcs", "replace")
        m = __import__("re").search(r"S-1-[0-9-]+", out)
        return m.group(0) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def username():
    return os.environ.get("USERNAME") or os.path.basename(os.path.expanduser("~"))


# ---------------------------------------------------------------- 回收站

class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p), ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int), ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p)]


FO_DELETE = 3
FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI, FOF_WANTNUKEWARNING = 0x4, 0x10, 0x40, 0x400, 0x4000


def bin_dir(path):
    """这个文件所在盘的回收站文件夹（只属于当前用户的那一份）。"""
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    sid = user_sid()
    return os.path.join(drive + "\\", "$Recycle.Bin", sid) if drive and sid else None


def _read_info(ipath):
    """读 $I 记录：返回 (原路径, 大小)。格式：版本(8) 大小(8) 删除时间(8) [v2: 路径长度(4)] 路径(UTF-16)。"""
    try:
        with open(ipath, "rb") as f:
            data = f.read(8 + 8 + 8 + 4 + 520 * 2 + 8)
    except OSError:
        return None, 0
    if len(data) < 24:
        return None, 0
    ver, size = struct.unpack_from("<qq", data, 0)
    if ver == 2 and len(data) >= 28:
        n = struct.unpack_from("<i", data, 24)[0]
        raw = data[28:28 + n * 2]
    else:  # 版本 1（Vista–8.1）：固定 520 个字符
        raw = data[24:24 + 520 * 2]
    return raw.decode("utf-16-le", "ignore").split("\x00", 1)[0], size


def long_path(p):
    """把 8.3 短名（C:\\Users\\RUNNER~1\\…，%TEMP% 常常是这种）展开成长名；回收站的记录里存的是长名。"""
    if not IS_WIN:
        return os.path.abspath(p)
    try:
        f = ctypes.windll.kernel32.GetLongPathNameW
        f.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        f.restype = ctypes.c_uint
        n = f(os.path.abspath(p), None, 0)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            if f(os.path.abspath(p), buf, n + 1):
                return buf.value
    except (OSError, AttributeError):
        pass
    return os.path.abspath(p)


def _find_entry(src, before, want=None):
    """在回收站里找刚移进去的 src：$I 记录里原路径相同、而且是这次新出现的。
    want 是移动前算好的长路径（文件已经不在了就没法再展开短名）。"""
    d = bin_dir(src)
    if not d:
        return None
    wants = {os.path.normcase(os.path.abspath(src))}
    if want:
        wants.add(os.path.normcase(want))
    fresh = []
    for ipath in sorted(glob.glob(os.path.join(d, "$I*")), key=lambda p: -os.path.getmtime(p)):
        if ipath in before:
            continue
        rpath = os.path.join(d, "$R" + os.path.basename(ipath)[2:])
        if not os.path.lexists(rpath):
            continue
        orig, _ = _read_info(ipath)
        if orig and os.path.normcase(orig) in wants:
            return rpath
        if orig and os.path.basename(orig).lower() == os.path.basename(src).lower():
            fresh.append(rpath)
    # 路径写法对不上（短名、大小写、符号链接）时：这次只新出现一条、名字也一样，就是它
    return fresh[0] if len(fresh) == 1 else None


def _tree_bytes(path, cap):
    """path 的大小（文件夹就加起来），超过 cap 就提前停（只为判断放不放得进回收站）。"""
    if not os.path.isdir(path) or os.path.islink(path):
        try:
            return os.lstat(path).st_size
        except OSError:
            return 0
    total = 0
    for d, dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                continue
            if total > cap:
                return total
    return total


def bin_limit(path):
    """这个盘的回收站设置：返回 (是否关掉了回收站, 最大容量字节或 None)。
    设置在 HKCU\\…\\Explorer\\BitBucket\\Volume\\{卷 GUID}：NukeOnDelete=1 表示「不移到回收站，直接删除」，MaxCapacity 单位 MB。"""
    if not IS_WIN:
        return False, None
    try:
        import winreg
        root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
        buf = ctypes.create_unicode_buffer(64)
        if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(ctypes.c_wchar_p(root), buf, 64):
            return False, None
        guid = buf.value[buf.value.index("{"):buf.value.index("}") + 1]
        key = "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\BitBucket\\Volume\\" + guid
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            def val(name):
                try:
                    return winreg.QueryValueEx(k, name)[0]
                except OSError:
                    return None
            nuke, cap = val("NukeOnDelete"), val("MaxCapacity")
        return nuke == 1, (int(cap) * 1048576 if cap else None)
    except (OSError, ValueError, AttributeError):
        return False, None


def recycle(src, check=None):
    """把 src 移进回收站，返回它在回收站里的路径（撤销时用）。check(src) 返回原因字符串时拒绝。
    跨盘不会发生（每个盘有自己的回收站）。系统在这些情况下会「直接删除」而不是进回收站，这里全部先拦住，绝不直接删：
    U 盘 / 网络盘（没有回收站）、这个盘关掉了回收站、东西比回收站的容量还大。"""
    if not IS_WIN:
        raise OSError(38, "只在 Windows 上可用")
    why = check(src) if check else None
    if why:
        raise OSError(1, why)
    src = os.path.abspath(src)
    if not os.path.lexists(src):
        raise OSError(2, "找不到这个文件")
    d = bin_dir(src)
    drive = os.path.splitdrive(src)[0] + "\\"
    # 从没往回收站放过东西的盘，<SID> 子文件夹还不存在，系统会在第一次放进去时建；
    # 只要是本机固定盘、根目录有 $Recycle.Bin 就行
    if not d or ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive)) != 3 or \
            not os.path.isdir(os.path.join(drive, "$Recycle.Bin")):
        raise OSError(1, "这个盘没有回收站（U 盘或网络盘），为了不直接删除，没有动它")
    nuke, cap = bin_limit(src)
    if nuke:
        raise OSError(1, "这个盘的回收站被设成了「直接删除」，为了不直接删除，没有动它")
    cap = cap if cap is not None else _default_cap(drive)
    if cap and _tree_bytes(src, cap) > cap:
        raise OSError(1, "它比这个盘的回收站容量还大，放进去会被系统直接删除，所以没有动它")
    want = long_path(src)
    with _lock:
        before = set(glob.glob(os.path.join(d, "$I*")))
        op = _SHFILEOPSTRUCTW(None, FO_DELETE, src + "\x00\x00", None,
                              FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING, 0, None, None)  # 万一系统还要直接删：弹窗问，不会悄悄删
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if rc != 0 or op.fAnyOperationsAborted:
            raise OSError(rc or 1, "没移进回收站（可能正被别的程序占用）")
        dst = _find_entry(src, before, want)
    if not dst:
        if os.path.lexists(src):
            raise OSError(1, "没移进回收站")
        return ""  # 移进去了，但没找到位置：资源管理器里仍能还原，只是 Deck 不能一键撤销
    return dst


def _default_cap(drive):
    """没改过设置时回收站默认最多占盘的 5%（Windows 10/11）。读不到盘大小就不限制。"""
    try:
        free = ctypes.c_ulonglong(0)
        total = ctypes.c_ulonglong(0)
        if ctypes.windll.kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(drive), None, ctypes.byref(total), ctypes.byref(free)):
            return total.value // 20
    except (OSError, AttributeError):
        pass
    return None


def restore(dst, src):
    """把回收站里的 dst 放回原位置 src（原位置已有东西就不放）。"""
    if not dst or not os.path.lexists(dst):
        raise OSError(2, "回收站里已经没有它了（可能被清空或在资源管理器里还原了）")
    with _lock:
        if os.path.lexists(src):
            raise OSError(17, "原位置已有同名东西")
        os.makedirs(os.path.dirname(src), exist_ok=True)
        os.rename(dst, src)
        info = os.path.join(os.path.dirname(dst), "$I" + os.path.basename(dst)[2:])
        try:  # 回收站的索引记录（Deck 刚才生成的），不拿掉的话回收站里会留一个打不开的空壳
            os.remove(info)
        except OSError:
            pass


def bin_bytes():
    """所有盘上当前用户回收站里一共多大（$R 文件的大小，读 $I 记录，不遍历大文件夹）。"""
    total = 0
    for drive in _fixed_drives():
        d = os.path.join(drive, "$Recycle.Bin", user_sid() or "")
        for ipath in glob.glob(os.path.join(d, "$I*")):
            total += max(0, _read_info(ipath)[1])
    return total


def _fixed_drives():
    if not IS_WIN:
        return []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    res = []
    for i in range(26):
        if mask & (1 << i):
            root = "%s:\\" % chr(65 + i)
            if ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) == 3:  # DRIVE_FIXED
                res.append(root)
    return res


# ---------------------------------------------------------------- 打开 / 显示

def reveal(path):
    """在资源管理器里选中它（相当于 Mac 的「在访达中显示」）。"""
    subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])  # noqa: S603,S607


def open_path(path):
    os.startfile(os.path.normpath(path))  # noqa: S606  只在用户点了「打开」时调用


def tilde(p):
    home = os.path.expanduser("~")
    if os.path.normcase(p).startswith(os.path.normcase(home)):
        return "~" + p[len(home):]
    return p


# ---------------------------------------------------------------- 凭据管理器（Mac 钥匙串的 Windows 版）

def cred_read(target):
    """读 Windows 凭据管理器里的一条「普通凭据」的密码（API key）。没有或出错返回 None，key 不进日志。

    用户自己在 PowerShell 里存：cmdkey /generic:deepseek-api /user:deck /pass
    （/pass 后面不写值，cmdkey 会提示输入、不回显、不进命令历史）。config.json 里 key_source 的
    {"type": "keychain", "service": "deepseek-api"} 在 Windows 上就读这条，和 Mac 钥匙串同一个名字。
    """
    if not IS_WIN or not target:
        return None

    class _CRED(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    _k32, adv = _dlls()
    p = ctypes.c_void_p()
    if not adv.CredReadW(str(target), 1, 0, ctypes.byref(p)) or not p.value:  # 1 = CRED_TYPE_GENERIC
        return None
    try:
        c = ctypes.cast(p, ctypes.POINTER(_CRED)).contents
        raw = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize) if c.CredentialBlobSize else b""
    finally:
        adv.CredFree(p)
    return _cred_text(raw)


def _cred_text(raw):
    """cmdkey 存的是 UTF-16LE；别的工具（Python keyring 等）可能存 UTF-8。API key 都是 ASCII：
    每隔一个字节是 0 才当 UTF-16，不然 UTF-8 的偶数长度 key 会被错解成一串汉字。"""
    utf16 = len(raw) >= 2 and len(raw) % 2 == 0 and not any(raw[1::2])
    try:
        v = raw.decode("utf-16-le" if utf16 else "utf-8").strip("\x00").strip()
    except UnicodeDecodeError:
        return None
    return v if v and v.isprintable() else None
