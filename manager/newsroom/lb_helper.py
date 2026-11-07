"""长桥行情小助手：单独跑在自己的虚拟环境里（装了官方 longbridge SDK），把行情写进一个 JSON 文件给管家服务读。

- 只读：只调行情、自选、资讯、市场温度这些查询接口，代码里没有任何下单 / 改单 / 改自选的调用。
- 授权走 OAuth（官方推荐），令牌由 SDK 自己存在 ~/.longbridge/openapi/tokens/<client_id>，我们不碰也不复制。
  授权页上只勾「自选」就够了（看行情不需要权限），不要勾交易权限。
- 平时（不带 --login）发现还没授权就写 need_auth 然后退出，绝不自己弹浏览器。
用法：python lb_helper.py --dir <状态目录> [--login]
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import date, datetime
from decimal import Decimal

REDIRECT_PORT = 60355
REGISTER_URL = "https://openapi.longbridge.com/oauth2/register"
INDICES = [(".IXIC.US", "纳斯达克"), (".SPX.US", "标普 500"), ("HSTECH.HK", "恒生科技"), ("000001.SH", "上证指数")]
MAX_SYMBOLS = 60
SERIES_MAX = 400

STATE = {"state": "starting", "updated": 0, "groups": [], "quotes": {}, "series": {}, "indices": [], "temp": {}, "news": [],
         "error": ""}
LOCK = threading.Lock()
DIRTY = threading.Event()
STOP = threading.Event()


def jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        try:
            return int(v.timestamp())
        except AttributeError:
            return v.isoformat()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    s = str(v)
    return s.split(".")[-1] if "." in s and " " not in s else s  # 枚举：TradeStatus.Normal → Normal


def num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def write_state(path):
    with LOCK:
        STATE["updated"] = int(time.time())
        data = json.dumps(STATE, ensure_ascii=False)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


def set_state(**kw):
    with LOCK:
        STATE.update(kw)
    DIRTY.set()


# 注册客户端时只申请「自选」权限（长桥的权限编号：4=watchlist，6=account.read，10=trade.read，11=trade.write）。
# 看行情本身不需要任何权限。11-07 第一次连接没限定权限，授权里带上了下单权限，所以改成只要 4。
SCOPE = "4"


def _trash(path):
    """移到废纸篓（绝不直接删除）。"""
    if not os.path.exists(path):
        return
    t = os.path.expanduser("~/.Trash")
    os.makedirs(t, exist_ok=True)
    os.replace(path, os.path.join(t, "%s-%d" % (os.path.basename(path), int(time.time()))))


def client_id(d):
    """client_id 不是秘密（公开客户端），第一次用时注册一个存起来。
    旧的注册没限定权限：换一个只要自选的新注册，旧令牌移到废纸篓（本机退出登录），需要他重新点一次「连接长桥」。"""
    p = os.path.join(d, "longbridge.json")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        cid = data.get("client_id")
        if cid and data.get("scope") == SCOPE:
            return cid
        if cid:
            _trash(os.path.expanduser("~/.longbridge/openapi/tokens/%s" % cid))
    except (OSError, ValueError):
        pass
    body = {"redirect_uris": ["http://localhost:%d/callback" % REDIRECT_PORT], "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "client_name": "Mac Deck",
            "scope": SCOPE}
    req = urllib.request.Request(REGISTER_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
        cid = json.loads(r.read()).get("client_id")
    if not cid:
        raise RuntimeError("长桥没返回 client_id")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"client_id": cid, "scope": SCOPE}, f)
    return cid


def token_scopes(cid):
    """只读令牌里的 scopes 字段（JWT 中间那段），令牌本身不打印、不复制。"""
    import base64
    try:
        with open(os.path.expanduser("~/.longbridge/openapi/tokens/%s" % cid), encoding="utf-8") as f:
            tok = json.load(f).get("access_token") or ""
        mid = tok.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(mid + "=" * (-len(mid) % 4)))
        sc = payload.get("scopes") or payload.get("scope") or []
        return sc.split() if isinstance(sc, str) else [str(x) for x in sc]
    except Exception:  # noqa: BLE001
        return []


def token_exists(cid):
    return os.path.exists(os.path.expanduser("~/.longbridge/openapi/tokens/%s" % cid))


def quote_row(q, name=""):
    row = {"name": name, "last": num(q.last_done), "prev_close": num(getattr(q, "prev_close", None)),
           "open": num(getattr(q, "open", None)), "high": num(getattr(q, "high", None)), "low": num(getattr(q, "low", None)),
           "ts": jsonable(getattr(q, "timestamp", None)), "status": jsonable(getattr(q, "trade_status", None))}
    for k, lab in (("pre_market_quote", "pre"), ("post_market_quote", "post"), ("overnight_quote", "night")):
        e = getattr(q, k, None)
        if e is not None and num(getattr(e, "last_done", None)):
            row[lab] = {"last": num(e.last_done), "ts": jsonable(getattr(e, "timestamp", None))}
    return row


def news_rows(items, symbol):
    """ContentContext.news 返回的字段没写在文档里：按常见字段名取，取不到就跳过这条。"""
    out = []
    for n in list(items or [])[:8]:
        g = (lambda *names: next((getattr(n, a) for a in names if getattr(n, a, None)), None))
        title = g("title", "name")
        if not title:
            continue
        out.append({"symbol": symbol, "id": str(g("id") or title)[:40], "title": str(title)[:200],
                    "summary": str(g("description", "summary", "content") or "")[:300],
                    "url": str(g("url", "detail_url", "link") or ""),
                    "ts": jsonable(g("published_at", "publish_at", "created_at", "updated_at"))})
    return out


def run(d, login):
    from longbridge.openapi import Config, OAuthBuilder, QuoteContext, SubType  # noqa: WPS433（只在虚拟环境里有）
    try:
        from longbridge.openapi import ContentContext
    except ImportError:
        ContentContext = None
    try:
        from longbridge.openapi import Market
    except ImportError:
        Market = None
    path = os.path.join(d, "market.json")
    cid = client_id(d)
    if not login and not token_exists(cid):
        set_state(state="need_auth", error="还没连接长桥")
        write_state(path)
        return 3

    def open_url(url):
        set_state(state="authorizing", auth_url=url)
        write_state(path)
        if login:
            subprocess.Popen(["open", url])  # 只有他在 Deck 里点了「连接长桥」才会走到这里

    oauth = OAuthBuilder(cid, REDIRECT_PORT).build(open_url)
    cfg = Config.from_oauth(oauth)
    ctx = QuoteContext(cfg)
    sc = token_scopes(cid)
    # 授权里要是带了交易 / 账户权限就提醒他（Deck 从不调用这些接口，但令牌本身有这个能力）
    set_state(state="ok", error="", auth_url="", scopes=sc,
              scope_warn=any(x.startswith("trade") or x.startswith("account") for x in sc))

    groups, names, symbols = [], {}, []
    for g in ctx.watchlist():
        syms = []
        for s in g.securities:
            names[s.symbol] = s.name
            syms.append(s.symbol)
            if s.symbol not in symbols:
                symbols.append(s.symbol)
        groups.append({"id": g.id, "name": g.name, "symbols": syms})
    symbols = symbols[:MAX_SYMBOLS]
    set_state(groups=groups)

    def refresh_quotes():
        rows = {}
        for i in range(0, len(symbols), 50):
            for q in ctx.quote(symbols[i:i + 50]):
                rows[q.symbol] = quote_row(q, names.get(q.symbol, ""))
        idx = []
        try:
            for q in ctx.quote([s for s, _ in INDICES]):
                r = quote_row(q, dict(INDICES).get(q.symbol, q.symbol))
                r["symbol"] = q.symbol
                idx.append(r)
        except Exception as e:  # noqa: BLE001（指数代码不认就不显示指数，不影响自选）
            print("[lb] 指数：%s" % e, file=sys.stderr)
        with LOCK:
            for k, v in rows.items():
                old = STATE["quotes"].get(k) or {}
                v["name"] = v["name"] or old.get("name", "")
                STATE["quotes"][k] = v
            if idx:
                STATE["indices"] = idx
        DIRTY.set()

    def refresh_series():
        for s in symbols[:24]:
            if STOP.is_set():
                return
            try:
                pts = [[jsonable(l.timestamp), num(l.price)] for l in ctx.intraday(s)]
                with LOCK:
                    STATE["series"][s] = pts[-SERIES_MAX:]
                DIRTY.set()
            except Exception as e:  # noqa: BLE001
                print("[lb] 分时 %s：%s" % (s, e), file=sys.stderr)

    def on_quote(symbol, ev):
        p = num(getattr(ev, "last_done", None))
        if p is None:
            return
        with LOCK:
            q = STATE["quotes"].setdefault(symbol, {"name": names.get(symbol, "")})
            q["last"] = p
            q["ts"] = jsonable(getattr(ev, "timestamp", None))
            ser = STATE["series"].setdefault(symbol, [])
            ser.append([q["ts"] or int(time.time()), p])
            del ser[:-SERIES_MAX]
        DIRTY.set()

    refresh_quotes()
    ctx.set_on_quote(on_quote)
    try:
        ctx.subscribe(symbols, [SubType.Quote])
    except Exception as e:  # noqa: BLE001（港股免费档不能推送：退回每 5 秒拉一次）
        print("[lb] 订阅：%s" % e, file=sys.stderr)
    threading.Thread(target=refresh_series, daemon=True).start()

    content = ContentContext(cfg) if ContentContext else None

    def refresh_slow():
        news = []
        if content:
            for s in symbols[:20]:
                try:
                    news.extend(news_rows(content.news(s), s))
                except Exception as e:  # noqa: BLE001
                    print("[lb] 资讯 %s：%s" % (s, e), file=sys.stderr)
                time.sleep(0.2)
        temp = {}
        if Market:
            for m, lab in ((getattr(Market, "US", None), "US"), (getattr(Market, "HK", None), "HK")):
                if m is None:
                    continue
                try:
                    t = ctx.market_temperature(m)
                    temp[lab] = {"value": num(t.temperature), "desc": str(t.description or "")}
                except Exception:  # noqa: BLE001
                    pass
        with LOCK:
            if news:
                STATE["news"] = news[:120]
            STATE["temp"] = temp
        DIRTY.set()

    last_slow = last_pull = 0.0
    while not STOP.is_set():
        now = time.time()
        if now - last_slow > 1800:
            last_slow = now
            threading.Thread(target=refresh_slow, daemon=True).start()
        if now - last_pull > 5:  # 推送之外每 5 秒拉一次快照（兜住没有推送权限的市场）
            last_pull = now
            try:
                refresh_quotes()
            except Exception as e:  # noqa: BLE001
                set_state(error=str(e)[:200])
        if DIRTY.is_set():
            DIRTY.clear()
            write_state(path)
        STOP.wait(1.0)
    return 0


def watchdog(login):
    """管家服务没了（被重启或退出）小助手也跟着退，免得两个小助手抢长桥唯一的长连接；
    授权模式下 10 分钟没授权完也退出，不一直占着回调端口。"""
    parent, t0 = os.getppid(), time.time()
    while not STOP.is_set():
        if os.getppid() != parent:
            os._exit(0)
        if login and STATE.get("state") in ("starting", "authorizing") and time.time() - t0 > 600:
            os._exit(4)
        time.sleep(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--login", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.dir, exist_ok=True)
    with open(os.path.join(a.dir, "lb_helper.pid"), "w") as f:
        f.write(str(os.getpid()))
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    threading.Thread(target=watchdog, args=(a.login,), daemon=True).start()
    try:
        code = run(a.dir, a.login)
    except Exception as e:  # noqa: BLE001
        set_state(state="error", error=str(e)[:300])
        write_state(os.path.join(a.dir, "market.json"))
        print("[lb] 出错：%s" % e, file=sys.stderr)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
