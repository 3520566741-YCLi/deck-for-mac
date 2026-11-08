"""Deck for Windows：下载语音模型（11-08 第三步）。

Mac 上是三个 shell 脚本（service/fetch_asr_models.sh、service/models/fetch_wake_models.sh）用 curl + tar 下载；
Windows 上没有这些，这里用标准库写一份，供语音服务第一次启动时自动调用，也供 Deck 设置里的按钮调用
（语音服务的 /models/status、/models/download，见 voice_launch.py）。模型不打进安装包。

- 来源：k2-fsa/sherpa-onnx 官方 GitHub Releases（和 Mac 脚本同一批文件、同样的目录名）
- 断点续传：先下到 models/.download/<名字>.part，断了下次从断的地方接着下（HTTP Range）
- 校验：大小 + SHA-256 都对上才用；不对就丢掉重下
- 压缩包：解到临时目录，只挑要用的文件，整个目录一次换上去（下到一半不会留下半套模型）
- 进度：progress(dict) 回调，约每 0.25 秒一次

放在哪：<语音目录>/models/<模型名>/（语音目录 = %APPDATA%\\Deck\\voice，可用 DECK_VOICE_ROOT 改），
名字和 service/config.py 的 find_model_dir / voicepaths.py / tts.py 找的一致。

命令行：python windows/voice_models.py [--root 目录] [--list] [wake asr tts qwen3 ...]
"""
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request

BASE = os.environ.get("DECK_VOICE_MODEL_BASE", "https://github.com/k2-fsa/sherpa-onnx/releases/download").rstrip("/")

# 每一项：url（相对 BASE）、size、sha256、装到 models/ 下哪里（dest），
# kind=file 直接放；kind=tar 解压后从 top 目录里按 keep 挑文件（keep 为空 = 全要），need = 装好后必须有的文件。
# 大小和 SHA-256 是 11-08 从官方 Releases 下载后算的。
ITEMS = {
    "silero_vad": {
        "label": "句尾判断（Silero VAD）",
        "url": "asr-models/silero_vad.onnx", "kind": "file",
        "size": 643854, "sha256": "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
        "dest": "silero_vad/silero_vad.onnx", "need": [],
    },
    "kws": {
        "label": "唤醒词 / 「停」（KWS zipformer）",
        "url": "kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2", "kind": "tar",
        "size": 32885699, "sha256": "68447f4fbc67e70eee3a93961f36e81e98f47aef73ce7e7ca00885c6cd3616a6",
        "top": "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20",
        "dest": "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20",
        # 和 Mac 的 fetch_wake_models.sh 一样只留这些：encoder / joiner 用 int8 chunk-16，decoder 只有 fp32
        "keep": ["tokens.txt", "en.phone", "encoder-*chunk-16*.int8.onnx", "joiner-*chunk-16*.int8.onnx",
                 "decoder-*chunk-16*.onnx", "test_wavs/*"],
        "need": ["tokens.txt", "en.phone"],
    },
    "speaker": {
        "label": "声纹（CAM++）",
        "url": "speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx", "kind": "file",
        "size": 28281138, "sha256": "f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11",
        "dest": "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx", "need": [],
    },
    "sensevoice": {
        "label": "语音识别（SenseVoice-Small int8）",
        "url": "asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09.tar.bz2", "kind": "tar",
        "size": 165783878, "sha256": "7305f7905bfcf77fa0b39388a313f3da35c68d971661a65475b56fb2162c8e63",
        "top": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09",
        "dest": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09",
        "keep": ["model.int8.onnx", "tokens.txt", "README.md"],
        "need": ["model.int8.onnx", "tokens.txt"],
    },
    "kokoro": {
        "label": "语音合成（Kokoro v1.1 中英 int8）",
        "url": "tts-models/kokoro-int8-multi-lang-v1_1.tar.bz2", "kind": "tar",
        "size": 147052523, "sha256": "259c9c8bc906d6078e4e0570421885dabb68b539c2f970fcbb890a58eeaf3e9c",
        "top": "kokoro-int8-multi-lang-v1_1",
        # tts.py 找的是 kokoro-multi-lang-v1_1/，没有 model.onnx 时自动用 model.int8.onnx（int8 版小一半多，CPU 够快）
        "dest": "kokoro-multi-lang-v1_1",
        "keep": [],
        "need": ["model.int8.onnx", "voices.bin", "tokens.txt", "espeak-ng-data", "lexicon-zh.txt"],
    },
    "qwen3": {
        "label": "语音识别·更准（Qwen3-ASR 0.6B int8，可选）",
        "url": "asr-models/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25.tar.bz2", "kind": "tar",
        "size": 878702423, "sha256": "393f8a14e2f5fb96746aaab342997a40641001fbd5bf9592a080a8329178ee96",
        "top": "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25",
        "dest": "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25",
        "keep": ["conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx", "tokenizer/*", "README.md"],
        "need": ["conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx", "tokenizer"],
    },
}

# 一组一组装：wake = 唤醒 + 「停」+ 声纹 + 句尾判断；asr = 听写；tts = 朗读；qwen3 = 可选的更准的听写（约 880MB）
PACKS = {
    "wake": {"label": "唤醒和声纹", "items": ["silero_vad", "kws", "speaker"]},
    "asr": {"label": "语音识别", "items": ["silero_vad", "sensevoice"]},
    "tts": {"label": "语音合成", "items": ["kokoro"]},
    "qwen3": {"label": "更准的语音识别（可选）", "items": ["silero_vad", "qwen3"]},
}
DEFAULT_PACKS = ("wake", "asr", "tts")   # 合计约 375MB

_lock = threading.Lock()


class Cancelled(Exception):
    pass


def default_root():
    """语音目录：和 service/config.py 的 ROOT 一样（DECK_VOICE_ROOT 优先）。"""
    if os.environ.get("DECK_VOICE_ROOT"):
        return os.environ["DECK_VOICE_ROOT"]
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    else:
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    return os.path.join(base, "Deck", "voice")


def models_dir(root=None):
    return os.path.join(root or default_root(), "models")


def _items_of(packs, items=None, pack_map=None):
    """包名或单个模型名 → 模型名列表（去重、保持顺序）。"""
    items, pack_map = items or ITEMS, pack_map or PACKS
    out = []
    for p in packs:
        names = pack_map[p]["items"] if p in pack_map else [p] if p in items else None
        if names is None:
            raise ValueError("没有这个模型包：%s" % p)
        out += [n for n in names if n not in out]
    return out


def installed(name, root=None, items=None):
    it = (items or ITEMS)[name]
    dest = os.path.join(models_dir(root), it["dest"])
    if it["kind"] == "file":
        return os.path.isfile(dest) and os.path.getsize(dest) == it["size"]
    return os.path.isdir(dest) and all(os.path.exists(os.path.join(dest, f)) for f in it["need"])


def status(root=None, items=None, packs=None):
    """每个包装没装、还要下多少。给 /models/status 和设置页用。"""
    items = items or ITEMS
    packs = packs or PACKS
    out = {"root": models_dir(root), "packs": {}, "items": {}}
    for n, it in items.items():
        out["items"][n] = {"label": it["label"], "installed": installed(n, root, items), "size": it["size"]}
    for p, pk in packs.items():
        miss = [n for n in pk["items"] if not out["items"][n]["installed"]]
        out["packs"][p] = {"label": pk["label"], "installed": not miss, "missing": miss,
                           "download_bytes": sum(items[n]["size"] for n in miss)}
    out["missing_default"] = [p for p in DEFAULT_PACKS if p in out["packs"] and not out["packs"][p]["installed"]]
    return out


# ---------------------------------------------------------------- 下载

def _sha256(path, h=None, stop=None):
    h = h or hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if stop is not None and stop.is_set():
                raise Cancelled()
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h


def _fetch(url, part, size, sha, report, stop, timeout=30, retries=4):
    """下到 part（断点续传），返回校验通过的 part 路径。"""
    for attempt in range(retries + 1):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have > size:  # 比应有的还大：坏了，重下
            os.remove(part)  # 自己下的半截文件，不是用户文件
            have = 0
        if have < size:
            req = urllib.request.Request(url, headers={"User-Agent": "Deck-voice-models/1"})
            if have:
                req.add_header("Range", "bytes=%d-" % have)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    if have and r.status != 206:  # 服务器不支持续传：从头来
                        have = 0
                    with open(part, "ab" if have else "wb") as f:
                        last = 0.0
                        while True:
                            if stop is not None and stop.is_set():
                                raise Cancelled()
                            b = r.read(256 * 1024)
                            if not b:
                                break
                            f.write(b)
                            have += len(b)
                            now = time.monotonic()
                            if now - last > 0.25:
                                last = now
                                report("download", have, size)
            except Cancelled:
                raise
            except (urllib.error.URLError, OSError, ValueError) as e:
                code = getattr(e, "code", None)
                if code == 416:  # 请求的范围超出：多半是已经下完了，下面按大小校验
                    pass
                elif attempt < retries:
                    report("retry", have, size, error=str(e))
                    time.sleep(min(30, 2 ** attempt))
                    continue
                else:
                    raise OSError("下载失败（%s）：%s" % (url, e)) from e
        have = os.path.getsize(part) if os.path.exists(part) else 0
        report("verify", have, size)
        if have == size and (not sha or _sha256(part, stop=stop).hexdigest() == sha):
            return part
        # 大小或校验和不对：丢掉重下
        try:
            os.remove(part)  # 自己下的文件，不是用户文件
        except OSError:
            pass
        if attempt >= retries:
            break
    raise OSError("下载的文件校验不对（%s），已丢弃，稍后再试" % url)


def _safe_members(tf, top):
    """只解 top/ 下面的普通文件和目录，挡住 ../ 和绝对路径、链接（压缩包是外来数据）。"""
    for m in tf.getmembers():
        name = m.name.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/") or not (name == top or name.startswith(top + "/")):
            continue
        if not (m.isfile() or m.isdir()):
            continue
        yield m


def _keep(rel, keep):
    if not keep:
        return True
    return any(fnmatch.fnmatch(rel, k) for k in keep)


def _install_tar(archive, it, mdir, tmpdir, stop, report):
    report("extract", 0, it["size"])
    work = os.path.join(tmpdir, "x-" + os.path.basename(it["dest"]))
    if os.path.isdir(work):
        shutil.rmtree(work, ignore_errors=True)  # 上次解到一半留下的临时目录（自己的）
    os.makedirs(work)
    top = it["top"]
    with tarfile.open(archive, "r:bz2") as tf:
        for m in _safe_members(tf, top):
            if stop is not None and stop.is_set():
                raise Cancelled()
            rel = m.name.replace("\\", "/")[len(top):].lstrip("/")
            if not rel or m.isdir() or not _keep(rel, it["keep"]):
                continue
            out = os.path.join(work, *rel.split("/"))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            src = tf.extractfile(m)
            if src is None:
                continue
            with src, open(out, "wb") as f:
                shutil.copyfileobj(src, f, 1 << 20)
    miss = [f for f in it["need"] if not os.path.exists(os.path.join(work, f))]
    if miss:
        raise OSError("压缩包里缺文件：%s" % ", ".join(miss))
    dest = os.path.join(mdir, it["dest"])
    if os.path.exists(dest):  # 旧的不完整的那份：挪到临时目录，换好新的再清掉（都是自己下的模型文件）
        old = os.path.join(tmpdir, "old-%d-%s" % (int(time.time()), os.path.basename(dest)))
        os.replace(dest, old)
        shutil.rmtree(old, ignore_errors=True)
    os.replace(work, dest)


def download(packs=DEFAULT_PACKS, root=None, progress=None, stop=None, items=None, pack_map=None, base=None):
    """下载这些包里还没装的模型。返回 {"ok", "installed": [...], "skipped": [...], "error"?}。
    progress(d)：d = {item, label, phase(download/retry/verify/extract/done), done, total, index, count, bytes_done, bytes_total}
    stop：threading.Event，set() 后尽快停下（已下的部分留着，下次续传）。同一时间只跑一个下载。"""
    items = items or ITEMS
    base = (base or BASE).rstrip("/")
    names = _items_of(packs, items, pack_map)
    todo = [n for n in names if not installed(n, root, items)]
    res = {"ok": True, "installed": [], "skipped": [n for n in names if n not in todo]}
    if not todo:
        return res
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "已经在下载了", "busy": True}
    try:
        mdir = models_dir(root)
        tmpdir = os.path.join(mdir, ".download")
        os.makedirs(tmpdir, exist_ok=True)
        total_bytes = sum(items[n]["size"] for n in todo)
        need = total_bytes * 2.2  # 压缩包 + 解出来的文件
        free = shutil.disk_usage(mdir).free
        if free < need:
            return {"ok": False, "error": "磁盘空间不够：要 %.0f MB，只剩 %.0f MB" % (need / 1e6, free / 1e6)}
        done_before = 0
        for i, n in enumerate(todo):
            it = items[n]

            def report(phase, done, total, error=None, _n=n, _i=i, _it=it, _base=done_before):
                if progress is None:
                    return
                d = {"item": _n, "label": _it["label"], "phase": phase, "done": done, "total": total,
                     "index": _i + 1, "count": len(todo), "bytes_done": _base + min(done, total), "bytes_total": total_bytes}
                if error:
                    d["error"] = error
                try:
                    progress(d)
                except Exception:  # noqa: BLE001  回调出错不影响下载
                    pass

            part = os.path.join(tmpdir, os.path.basename(it["url"]) + ".part")
            _fetch(base + "/" + it["url"], part, it["size"], it.get("sha256"), report, stop)
            if it["kind"] == "file":
                dest = os.path.join(mdir, it["dest"])
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                os.replace(part, dest)
            else:
                _install_tar(part, it, mdir, tmpdir, stop, report)
                os.remove(part)  # 压缩包已经解好（自己下的临时文件，不是用户文件）
            done_before += it["size"]
            report("done", it["size"], it["size"])
            res["installed"].append(n)
        return res
    except Cancelled:
        return dict(res, ok=False, error="已取消（下次接着下）", cancelled=True)
    except Exception as e:  # noqa: BLE001
        return dict(res, ok=False, error=str(e) or repr(e))
    finally:
        _lock.release()


def busy():
    return _lock.locked()


def main(argv=None):
    import argparse  # noqa: PLC0415
    ap = argparse.ArgumentParser(description="下载 Deck 语音模型")
    ap.add_argument("--root", help="语音目录（默认 %%APPDATA%%\\Deck\\voice）")
    ap.add_argument("--list", action="store_true", help="只看装了哪些")
    ap.add_argument("packs", nargs="*", default=list(DEFAULT_PACKS), help="wake asr tts qwen3（默认前三个）")
    a = ap.parse_args(argv)
    if a.list:
        print(json.dumps(status(a.root), ensure_ascii=False, indent=2))
        return 0
    last = [None]

    def show(d):
        key = (d["item"], d["phase"], int(100 * d["done"] / max(1, d["total"])) // 5)
        if key != last[0]:
            last[0] = key
            print("[%d/%d] %s %s %.1f/%.1f MB" % (d["index"], d["count"], d["label"], d["phase"],
                                                   d["done"] / 1e6, d["total"] / 1e6), flush=True)
    r = download(a.packs, a.root, show)
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
