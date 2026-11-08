"""日报里的股价核对：编稿时把长桥快照交给编辑，编完再逐句核一遍。

11-08：口播稿写「英伟达股价回落到约 202 美元」，长桥实际是 186.64。新闻原文里的价格常常是几天前的，
模型还会自己补数字。现在两道防线：
1. 编稿时把长桥自选的实时快照放进用户消息，规定现价只能用快照里的；没有快照就不写具体股价。
2. 编完逐句核：提到自选股、又写了「xx 美元 / 港元」的句子，数字和快照（现价、昨收、盘前盘后、日内高低）
   差超过 3% 就删掉那一句（口播）或那一小句（标题摘要）。目标价、市值、营收这类不是股价的数字不管。
"""
import re
import time

FRESH = 36 * 3600          # 快照超过一天半没更新就不当真
TOL = 0.03                 # 和快照差 3% 以内算对（盘中会动）
NOT_PRICE = re.compile(r"目标价|目标|市值|营收|收入|利润|估值|融资|订单|投资|成本|售价|定价|月费|年费|每股收益|EPS|分红|股息|回购|薪|亿|万")
MONEY = re.compile(r"(?<![\d.])(\d{1,5}(?:\.\d{1,3})?)\s*(美元|美金|港元|港币|港元|元|块)")
# 像在说股价的词。没有这些词时，只有数字和现价差不多（0.5–1.5 倍）才当成股价核，免得把「1299 美元的新机」当股价删掉
STOCK_WORD = re.compile(r"股价|股票|现价|报价|收在|收于|收报|收盘|开盘|盘前|盘后|盘中|涨到|涨至|跌到|跌至|回落|反弹|站上|跌破|突破|每股|一股")
TICKERS = {  # 新闻里常见的英文写法
    "NVDA": ["Nvidia", "NVIDIA", "英伟达"], "TSLA": ["Tesla", "特斯拉"], "AAPL": ["Apple", "苹果"],
    "MSFT": ["Microsoft", "微软"], "TSM": ["TSMC", "台积电"], "GOOGL": ["Google", "谷歌", "Alphabet"],
    "GOOG": ["Google", "谷歌", "Alphabet"], "AMZN": ["Amazon", "亚马逊"], "META": ["Meta"], "AMD": ["AMD"],
    "700": ["腾讯"], "9988": ["阿里"], "1810": ["小米"], "3690": ["美团"],
}


def snapshot(market, now=None):
    """长桥快照 → {symbol: {"name", "names", "prices", "line"}}。没有、过期或出错就返回空。"""
    if not isinstance(market, dict) or market.get("state") != "ok":
        return {}
    if (now or time.time()) - (market.get("updated") or 0) > FRESH:
        return {}
    out = {}
    for sym, q in (market.get("quotes") or {}).items():
        if not isinstance(q, dict) or not isinstance(q.get("last"), (int, float)) or q["last"] <= 0:
            continue
        base = sym.split(".")[0]
        name = str(q.get("name") or base)
        names = {name, base, re.sub(r"[-－].*$|控股$|集团$", "", name)} | set(TICKERS.get(base, []))
        prices = [q["last"]] + [q.get(k) for k in ("prev_close", "open", "high", "low")]
        for k in ("pre", "post"):
            if isinstance(q.get(k), dict):
                prices.append(q[k].get("last"))
        prices = [float(p) for p in prices if isinstance(p, (int, float)) and p > 0]
        cur = "港元" if sym.endswith(".HK") else "美元"
        chg = ""
        if q.get("prev_close"):
            chg = "，较昨收 %+.2f%%" % ((q["last"] / q["prev_close"] - 1) * 100)
        out[sym] = {"name": name, "hk": sym.endswith(".HK"), "names": sorted((n for n in names if n), key=len, reverse=True), "prices": prices,
                    "line": "%s（%s）现价 %.2f %s%s" % (name, sym, q["last"], cur, chg)}
    return out


def prompt_block(snap, updated):
    if not snap:
        return "长桥行情现在拿不到：口播和摘要里不要写任何股票的具体价格（涨跌原因可以讲）。"
    t = time.strftime("%m-%d %H:%M", time.localtime(updated or time.time()))
    return ("长桥自选的实时行情（%s 更新）。提到这些股票的现价只能用这里的数字；新闻里的股价可能是几天前的，不要照抄；"
            "分析师目标价可以引用新闻，但要说明是目标价：\n%s" % (t, "\n".join("- " + v["line"] for v in snap.values())))


CLAUSE = re.compile(r"[^，。；！？,;!?]+")


def _bad(text, snap):
    """这句话里有没有和快照对不上的股价。返回第一个对不上的说明，没有就返回空。
    按小句核，而且只拿数字前面最近提到的那只自选股比：「英伟达现价 186 美元，腾讯收在 520 港元」里，
    腾讯不在自选的话 520 不算到英伟达头上（11-08 实测误删过整句）。"""
    last = None  # 前面小句最后提到的自选股（「英伟达今天大涨，收在 202 美元」）
    for c in CLAUSE.finditer(text):
        why, last = _bad_clause(c.group(0), snap, last)
        if why:
            return why
    return ""


def _named(text, snap, end):
    best, at = None, -1
    for v in snap.values():
        for n in v["names"]:
            i = text.rfind(n, 0, end)
            if i > at:
                best, at = v, i
    return best


def _bad_clause(text, snap, last):
    for m in MONEY.finditer(text):
        # 数字前面最近出现的自选股名字；这一小句没提到，就用前面小句提到的那只，但只核和现价差不多的数字
        best = _named(text, snap, m.start())
        near_only = False
        if not best:
            best, near_only = last, True
        if not best:
            continue
        hit = [best]
        before = text[max(0, m.start() - 8):m.start()]
        after = text[m.end():m.end() + 2]
        if NOT_PRICE.search(before) or after[:1] in ("亿", "万"):
            continue
        x = float(m.group(1))
        if (m.group(2) in ("港元", "港币")) != best["hk"]:
            continue  # 币种对不上，说的不是这只
        if (near_only or not STOCK_WORD.search(text)) and not any(0.5 < x / p < 1.5 for v in hit for p in v["prices"]):
            continue
        if not any(abs(x / p - 1) <= TOL for v in hit for p in v["prices"]):
            return "%s ≠ %s" % (m.group(0), "、".join(v["line"] for v in hit)), last
    return "", _named(text, snap, len(text)) or last


def check_lines(lines, snap):
    """口播稿：对不上的整句删掉。返回 (新列表, 删掉的说明)。"""
    if not snap:
        return lines, []
    keep, fixes = [], []
    for l in lines:
        why = _bad(l, snap)
        if why:
            fixes.append("删掉口播「%s」：%s" % (l, why))
        else:
            keep.append(l)
    return keep, fixes


def check_text(text, snap):
    """标题摘要：只删对不上的那一小句（按逗号、句号切），其它留着。"""
    if not snap or not text:
        return text, []
    parts = re.split(r"(?<=[，。；！？,;])", text)
    out, fixes = [], []
    for p in parts:
        why = _bad(p, snap)
        if why:
            fixes.append("删掉「%s」：%s" % (p, why))
        else:
            out.append(p)
    new = "".join(out).strip("，,；; ")
    if new and new[-1] in "，,；;":
        new = new[:-1] + "。"
    return new, fixes


def check_edition(body, snap):
    """整期核一遍（口播、头版、各栏标题摘要、投资）。原地改，返回改了什么。"""
    if not snap:
        return []
    fixes = []
    body["script"], f = check_lines(body.get("script") or [], snap)
    fixes += f

    def story(s):
        if isinstance(s, dict):
            for k in ("title", "dek"):
                if s.get(k):
                    s[k], f2 = check_text(s[k], snap)
                    fixes.extend(f2)

    fr = body.get("front") or {}
    for s in [fr.get("lead")] + (fr.get("briefs") or []) + (fr.get("stories") or []):
        story(s)
    fq = body.get("frontier") or {}
    for s in [fq.get("lead")] + [i for b in fq.get("beats") or [] for i in b.get("items") or []] + (fq.get("papers") or []):
        story(s)
    for g in body.get("giants") or []:
        for s in g.get("items") or []:
            story(s)
    if body.get("giants_week"):
        body["giants_week"], f = check_text(body["giants_week"], snap)
        fixes += f
    for s in (body.get("picks") or []) + (body.get("invest") or []):
        story(s)
    art = body.get("art") or {}
    for s in [art.get("lead")] + (art.get("items") or []):
        story(s)
    return fixes
