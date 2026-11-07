"""监控台公用小工具。只用标准库，兼容 macOS 自带 python3 (3.9)。"""
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")


def run(cmd, timeout=10, env=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:  # noqa: BLE001
        return -1, "", str(e)


def expand(path):
    return os.path.expanduser(path) if path else path


def find_exe(name, extra=()):
    """在常见位置找可执行文件：配置的路径 > PATH > Homebrew > ~/.local/bin。"""
    for p in extra:
        p = expand(p)
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    hit = shutil.which(name)
    if hit:
        return hit
    for d in ("/opt/homebrew/bin", "/usr/local/bin", os.path.join(HOME, ".local/bin"), "/usr/bin"):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def http_json(url, headers=None, timeout=10):
    """GET 一个 JSON。返回 (status, obj)；网络失败时 status 为 None。"""
    req = urllib.request.Request(url, headers=dict({"User-Agent": "deck-monitor/1.0"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:  # noqa: BLE001
        return None, None


def num(v, default=None):
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return default
    return default


class SourceError(Exception):
    """采集失败时抛出：message 是给人看的中文说明（说清楚下一步怎么做）。"""
