"""DeepSeek：
- 本地部分（免费、随时刷新）：DeepSeek Harness 的 ~/.dsh 成本账本与会话元数据（只读标题/时间/模型，不读正文和凭据）。
- 余额（DeepSeek /user/balance 接口，查询本身不扣费）：按配置间隔查询，或在监控台上手动点“查余额”。
  监控台这张卡片只管 DeepSeek Harness：余额用 Harness 自己的 key（~/.dsh/.credentials.yaml 的 DEEPSEEK_API_KEY），
  Vesper 自己的 key 和用量只在 Deck 设置里看（用户 11-05 要求两者分开）。key 不会出现在页面和日志里。
- 用量：桌面版的 cost-meter 账本 + 命令行/委派方式跑的会话（sessions/**/session.v4.jsonl.zstd 里每一步的 usage）。
"""
import glob
import re
import shlex
import json
import os
import time
from datetime import datetime, timedelta

from .util import HOME, SourceError, find_exe, http_json, num, run

DSH = os.path.join(HOME, ".dsh")
SESSIONS = "storages/session_projcache/sessions"
LEDGER = "storages/cost-meter/ledger.json"
SYMBOL = {"CNY": "¥", "USD": "$", "EUR": "€"}
CREDS = ".credentials.yaml"
TS_KEYS = ("ts", "time", "timestamp", "at", "createdAt", "endedAt", "startedAt")


APP_PLIST = "/Applications/DeepSeek Harness.app/Contents/Info.plist"


def harness_homes():
    """Harness 有两个「家」，各有一份 key：命令行 dsh 用 $DSH_HOME 或 ~/.dsh；桌面版 App 用 Info.plist 里 LSEnvironment 的 DSH_HOME。"""
    homes = [("命令行", os.environ.get("DSH_HOME") or DSH)]
    try:
        import plistlib
        with open(APP_PLIST, "rb") as f:
            d = (plistlib.load(f).get("LSEnvironment") or {}).get("DSH_HOME")
        if d:
            homes.append(("桌面版", os.path.expanduser(d)))
    except Exception:  # noqa: BLE001
        pass
    seen, out = set(), []
    for name, h in homes:
        rp = os.path.realpath(h)
        if rp not in seen:
            seen.add(rp)
            out.append((name, h))
    return out


def read_key(home):
    """只读 .credentials.yaml 里扁平的 DEEPSEEK_API_KEY: xxx 这一行。"""
    try:
        with open(os.path.join(home, CREDS), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*DEEPSEEK_API_KEY\s*:\s*[\"']?([^\"'\s#]+)", line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def harness_key():
    for _, h in harness_homes():
        k = read_key(h)
        if k:
            return k
    return os.environ.get("DEEPSEEK_API_KEY") or None


def fix_cmd(paths):
    """key 失效时卡片里给的一行命令：运行后粘贴新 key（输入不显示、不进命令历史；直接回车＝用钥匙串里 Vesper 那把），
    先验证能用，再只改每个 .credentials.yaml 的 DEEPSEEK_API_KEY 那一行，旧文件先复制一份到废纸篓。"""
    code = ('import getpass,os,re,shutil,subprocess,time,urllib.request as u\n'
            'k=getpass.getpass("粘贴新的 DeepSeek key，然后回车（输入时不显示；直接回车＝用 Vesper 那把）：").strip()'
            ' or subprocess.run(["security","find-generic-password","-s","deepseek-api","-w"],capture_output=True,text=True).stdout.strip()\n'
            'try: ok=u.urlopen(u.Request("https://api.deepseek.com/user/balance",headers={"Authorization":"Bearer "+k}),timeout=15).status==200\n'
            'except Exception as e: ok=0;print("这把 key 不能用：",e)\n'
            'for p in (%s if ok else []):\n'
            ' s=open(p).read() if os.path.exists(p) else ""\n'
            ' os.path.exists(p) and shutil.copy2(p,os.path.expanduser("~/.Trash/"+os.path.basename(os.path.dirname(p))+"-credentials.yaml.bak-"+time.strftime("%%Y%%m%%d%%H%%M%%S")))\n'
            ' n,c=re.subn(r"(?m)^DEEPSEEK_API_KEY\\s*:.*$",lambda m:"DEEPSEEK_API_KEY: "+k,s)\n'
            ' open(p,"w").write(n if c else s.rstrip("\\n")+"\\nDEEPSEEK_API_KEY: "+k+"\\n");os.chmod(p,0o600);print("已换好：",p)\n'
            'ok and print("旧文件备份在废纸篓。重开 DeepSeek Harness，再在 Deck 上点「查余额」。")') % repr(list(paths))
    return "/usr/bin/python3 -c " + shlex.quote(code)


def sync_key(good_home, bad_home):
    """命令行 Harness（~/.dsh）的 key 失效、桌面版那把能用时，把能用的那把写进命令行那份（11-06 根因修复：
    协作台委派走 ~/.dsh，Harness 先读 .credentials.yaml 再看环境变量，失效的 key 会让委派一直 401）。
    只改 DEEPSEEK_API_KEY 那一行；旧文件先复制一份到废纸篓；key 不进日志。只写 ~/.dsh，绝不写桌面版那个家（在 6-官方端基线 里）。"""
    if os.path.realpath(bad_home) != os.path.realpath(os.environ.get("DSH_HOME") or DSH):
        return False
    k = read_key(good_home)
    if not k:
        return False
    p = os.path.join(bad_home, CREDS)
    try:
        s = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
        if s:
            import shutil
            shutil.copy2(p, os.path.join(HOME, ".Trash", "dsh-credentials.yaml.bak-" + time.strftime("%Y%m%d%H%M%S")))
        n, c = re.subn(r"(?m)^\s*DEEPSEEK_API_KEY\s*:.*$", lambda m: "DEEPSEEK_API_KEY: " + k, s)
        tmp = p + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(n if c else s.rstrip("\n") + ("\n" if s else "") + "DEEPSEEK_API_KEY: " + k + "\n")
        os.replace(tmp, p)
        os.chmod(p, 0o600)
        return True
    except OSError:
        return False


def _ms(v):
    v = num(v)
    if v is None:
        return None
    return v / 1000 if v > 1e11 else v


class DeepSeekSource:
    key = "deepseek"
    title = "DeepSeek"
    default_interval = 15

    def __init__(self, cfg, key_getter=None):
        self.cfg = cfg or {}
        # 默认用 Harness 的 key；config 里 "key": "butler" 才改用管家/Vesper 的 key
        self.key_getter = key_getter if self.cfg.get("key") == "butler" else harness_key
        self.usage_cache = {}  # 会话文件 -> (mtime, size, {day: tokens})
        self.balance = None
        self.balance_at = 0
        self.balance_err = None
        self.keys = []  # [{where, status}]，哪个家的 key 能不能用
        self.fix_cmd = None
        self.synced = {}  # 已经自动换过 key 的家（每次启动每个家最多换一次）
        self.fixed = None

    def _sessions(self):
        d = os.path.join(DSH, SESSIONS)
        out = []
        try:
            names = [n for n in os.listdir(d) if n.endswith(".json")]
        except OSError:
            return out
        for n in names:
            try:
                with open(os.path.join(d, n), encoding="utf-8") as f:
                    rows = json.load(f)["record"]["rows"]
            except Exception:  # noqa: BLE001
                continue
            meta = (rows.get("sessionListMetadata") or {}).get("val") or {}
            if meta.get("blank", True):
                continue
            last = _ms(meta.get("lastPromptAt"))
            if not last:
                continue
            sel = ((rows.get("modelSelection") or {}).get("val") or {}).get("lastUsed") or {}
            title = (rows.get("title") or {}).get("val")
            out.append({"title": (title or "").strip() or "未命名会话", "model": sel.get("model"), "last": last})
        return sorted(out, key=lambda s: -s["last"])

    def _ledger(self):
        try:
            with open(os.path.join(DSH, LEDGER), encoding="utf-8") as f:
                led = json.load(f)
        except Exception:  # noqa: BLE001
            return None
        cfg = led.get("config") or {}
        days = led.get("days") or {}
        rate = num(cfg.get("exchangeRate"), 1) or 1
        convert = cfg.get("pricingCurrency") and cfg.get("currency") and cfg["pricingCurrency"] != cfg["currency"]
        factor = rate if convert else 1
        today = datetime.now()
        tkey = today.strftime("%Y-%m-%d")
        wkey = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
        res = {"symbol": cfg.get("symbol") or "¥", "today": 0.0, "week": 0.0, "tokens": 0, "calls": 0, "days7": []}
        for k in sorted(days):
            e = days[k] if isinstance(days[k], dict) else {}
            c = num(e.get("cost"))
            if c is not None and wkey <= k <= tkey:
                res["week"] += c * factor
                if k == tkey:
                    res["today"] = c * factor
            res["tokens"] += sum(int(num(e.get(f), 0)) for f in ("input", "output", "cacheRead", "cacheWrite"))
            res["calls"] += int(num(e.get("calls"), 0))
        for i in range(6, -1, -1):
            k = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            c = num((days.get(k) or {}).get("cost"), 0) if isinstance(days.get(k), dict) else 0
            res["days7"].append(round(c * factor, 4))
        res["last_day"] = max(days) if days else None
        ref = led.get("balanceRef") or {}
        if num(ref.get("total")) is not None:
            res["cached_balance"] = {"amount": num(ref["total"]), "currency": ref.get("currency") or "CNY",
                                     "at": _ms(ref.get("at"))}
        return res

    def _check(self, key):
        return http_json(self.cfg.get("base_url", "https://api.deepseek.com").rstrip("/") + "/user/balance",
                         {"Authorization": "Bearer " + key, "Accept": "application/json"}, timeout=8)

    def _fetch_balance(self):
        homes, self.keys, status, body = [], [], None, None
        if self.cfg.get("key") == "butler":
            key = self.key_getter() if self.key_getter else None
            status, body = self._check(key) if key else ("missing", None)
        else:
            # 命令行版和桌面版各测一次；余额用第一把能用的 key 查（同一个账户，余额一样）
            homes = harness_homes()
            for name, h in homes:
                k = read_key(h)
                st, bd = self._check(k) if k else ("missing", None)
                self.keys.append({"where": name, "status": st})
                if st == 200 and body is None:
                    status, body = st, bd
            if body is None:
                status = next((k["status"] for k in self.keys if k["status"] != "missing"), "missing")
            # 一边能用、命令行那边 401/没填：自动把能用的写过去，再测一次；每个失效状态只试一次，不来回折腾
            good = next((h for (n, h), k in zip(homes, self.keys) if k["status"] == 200), None)
            for (n, h), k in zip(homes, self.keys):
                if good and k["status"] in (401, "missing") and h != good and not self.synced.get(h):
                    self.synced[h] = True
                    if sync_key(good, h):
                        st, _ = self._check(read_key(h))
                        k["status"], k["fixed_at"] = st, time.time()
                        self.fixed = {"where": n, "at": time.time(), "ok": st == 200}
        bad = [k["where"] for k in self.keys if k["status"] in (401, "missing")]
        self.fix_cmd = fix_cmd([os.path.join(h, CREDS) for n, h in homes if n in bad]) if bad else None
        if status == "missing":
            self.balance_err = "没找到 DeepSeek Harness 的 API key（.credentials.yaml 里的 DEEPSEEK_API_KEY）"
            self.balance = None
            return
        if status == 401:
            self.balance_err = "DeepSeek Harness 的 API key 失效了（401）"
            self.balance = None
            return
        if status != 200 or not isinstance(body, dict) or not body.get("balance_infos"):
            self.balance_err = "余额接口暂时不可用（%s），稍后再试" % (status or "网络错误")
            return
        infos = {(i.get("currency") or "CNY"): i for i in body["balance_infos"] if isinstance(i, dict)}
        b = infos.get("CNY") or next(iter(infos.values()))
        self.balance = {"amount": num(b.get("total_balance")), "currency": b.get("currency") or "CNY",
                        "granted": num(b.get("granted_balance")), "topped_up": num(b.get("topped_up_balance")),
                        "available": bool(body.get("is_available", True)), "at": time.time()}
        # 美元和人民币都显示（用户 11-05 要求）：接口给了两种就都用原值；只给一种就按汇率折算另一种，标「约」
        rate = self._usd_cny()
        both = {}
        for cur in ("CNY", "USD"):
            if cur in infos:
                both[cur] = {"amount": num(infos[cur].get("total_balance")), "approx": False}
        if "CNY" in both and "USD" not in both and both["CNY"]["amount"] is not None:
            both["USD"] = {"amount": round(both["CNY"]["amount"] / rate, 2), "approx": True}
        if "USD" in both and "CNY" not in both and both["USD"]["amount"] is not None:
            both["CNY"] = {"amount": round(both["USD"]["amount"] * rate, 2), "approx": True}
        self.balance["both"] = both
        self.balance["rate"] = rate
        # 两种货币是同一个账户的钱：按人民币合计判断够不够用（比如 ¥-0.15 + $11.36 其实还有钱）
        self.balance["total_cny"] = round(sum(num(i.get("total_balance"), 0) * (rate if c == "USD" else 1) for c, i in infos.items()), 2)
        self.balance_err = None

    def _session_usage(self):
        """命令行 / 从 Codex 委派跑的 Harness 会话不记 cost-meter 账本，从会话日志里把每一步的 token 加起来（按天）。
        会话是 zstd 压缩的 jsonl，用 Homebrew 的 zstd 解压；按文件修改时间缓存，没变的不重读。"""
        zstd = find_exe("zstd")
        files = []
        for _, h in harness_homes():
            files += glob.glob(os.path.join(h, "sessions", "*", "*", "session.v4.jsonl.zstd"))
        if not zstd or not files:
            return None
        cutoff = time.time() - 40 * 86400
        days = {}
        for fn in files:
            try:
                st = os.stat(fn)
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            c = self.usage_cache.get(fn)
            if not c or c[0] != st.st_mtime or c[1] != st.st_size:
                code, out, _ = run([zstd, "-dcq", fn], timeout=20)
                per, steps = {}, {}
                if code == 0:
                    fallback = time.strftime("%Y-%m-%d", time.localtime(st.st_mtime))
                    for line in out.splitlines():
                        if '"usage"' not in line:
                            continue
                        try:
                            o = json.loads(line)
                        except ValueError:
                            continue
                        # usage 在 assistant/message 行（type/time/data.usage）；同一步可能有多条，按 (turn, step) 只取最后一条
                        if o.get("type") not in ("assistant/message", "step/end"):
                            continue
                        dd = o.get("data") or {}
                        u = dd.get("usage") or {}
                        if not u:
                            continue
                        tok = num(u.get("totalTokens")) or sum(num(u.get(k), 0) for k in ("inputTokens", "outputTokens"))
                        ts = next((_ms(src.get(k)) for src in (o, o.get("data") or {}) for k in TS_KEYS
                                   if isinstance(src.get(k), (int, float))), None)
                        if ts is None:
                            for src in (o, o.get("data") or {}):
                                for k in TS_KEYS:
                                    if isinstance(src.get(k), str):
                                        try:
                                            ts = datetime.fromisoformat(src[k].replace("Z", "+00:00")).timestamp()
                                        except ValueError:
                                            pass
                                        break
                                if ts:
                                    break
                        day = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else fallback
                        sid = (dd.get("turn"), dd.get("step")) if dd.get("step") is not None else ("seq", o.get("seq"))
                        steps[sid] = (day, int(tok or 0))
                    for day, tok in steps.values():
                        e = per.setdefault(day, [0, 0])
                        e[0] += tok
                        e[1] += 1
                c = (st.st_mtime, st.st_size, per)
                self.usage_cache[fn] = c
            for d, (t, n) in c[2].items():
                e = days.setdefault(d, [0, 0])
                e[0] += t
                e[1] += n
        return days

    def _usd_cny(self):
        """美元兑人民币汇率：优先用 config 里的 usd_cny，其次 Harness 账本里的 exchangeRate，最后用 7.1。"""
        r = num(self.cfg.get("usd_cny"))
        if not r:
            try:
                with open(os.path.join(DSH, LEDGER), encoding="utf-8") as f:
                    r = num(((json.load(f) or {}).get("config") or {}).get("exchangeRate"))
            except Exception:  # noqa: BLE001
                r = None
        return r if r and 5 < r < 10 else 7.1

    def collect(self, force=False):
        now = time.time()
        every = float(self.cfg.get("balance_interval_min", 5)) * 60
        if force or (every > 0 and now - self.balance_at > every):
            self.balance_at = now
            self._fetch_balance()
        has_dsh = os.path.isdir(DSH)
        if not has_dsh and not self.balance and self.balance_err:
            raise SourceError(self.balance_err)
        sessions = self._sessions() if has_dsh else []
        su = self._session_usage() if has_dsh else None
        usage = None
        if su is not None:
            today = datetime.now()
            tkey = today.strftime("%Y-%m-%d")
            wkey = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
            usage = {"today": su.get(tkey, [0, 0])[0], "week": sum(v[0] for d, v in su.items() if wkey <= d <= tkey),
                     "steps_week": sum(v[1] for d, v in su.items() if wkey <= d <= tkey),
                     "days7": [su.get((today - timedelta(days=i)).strftime("%Y-%m-%d"), [0, 0])[0] for i in range(6, -1, -1)],
                     "last_day": max((d for d, v in su.items() if v[0]), default=None)}
        code, out, _ = run(["pgrep", "-f", "DeepSeek Harness.app/Contents/MacOS"], timeout=3)
        running = code == 0 and bool(out.strip())
        active = [s for s in sessions if now - s["last"] <= 15 * 60]
        bal = self.balance
        level = "ok"
        if bal and bal["amount"] is not None:
            low = float(self.cfg.get("low_balance", 10))
            total = bal.get("total_cny", bal["amount"])
            level = "error" if total <= 0 or not bal["available"] else "warn" if total < low else "ok"
            if level == "ok" and any(k["status"] in (401, "missing") for k in self.keys):
                level = "warn"  # 有一边（命令行或桌面版）的 key 不能用
        elif self.balance_err:
            level = "warn"
        why = []
        if bal and bal["amount"] is not None:
            if total <= 0 or not bal["available"]:
                why.append({"text": "DeepSeek 余额不足，API 用不了", "fix": "去 platform.deepseek.com 充值"})
            elif total < low:
                why.append({"text": "DeepSeek 余额只剩约 ¥%.2f" % total, "fix": "快用完了，记得充值"})
        elif self.balance_err:
            why.append({"text": "查不到 DeepSeek 余额：%s" % str(self.balance_err)[:60], "fix": "点卡片看怎么修"})
        for k in self.keys:
            if k["status"] in (401, "missing"):
                why.append({"text": "%s Harness 的 key %s" % (k["where"], "失效（401）" if k["status"] == 401 else "没填"),
                            "fix": "点卡片，复制那行命令到终端，粘贴新 key"})
        cur = (bal or {}).get("currency") or "CNY"
        return {"level": level, "why": why, "balance": bal, "balance_error": self.balance_err, "fix_cmd": self.fix_cmd, "keys": self.keys, "key_fixed": self.fixed,
                "symbol": SYMBOL.get(cur, cur + " "),
                "usd_cny": self._usd_cny(),
                "harness": {"installed": has_dsh, "running": running, "active": len(active),
                            "sessions": sessions[:5], "ledger": self._ledger() if has_dsh else None, "usage": usage}}
