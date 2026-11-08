"""DeepSeek 当「总编辑」：一期只调用一次，把免费来源抓到的候选挑出来、分栏、写中文标题摘要、写早报口播稿。

省钱规则（用户 11-07 要求）：
- 只在 DeepSeek 非高峰（半价）时段自动编排；高峰 = 北京时间工作日 9–12 点、14–18 点。
- 固定的系统提示词放在最前面，吃 DeepSeek 默认开着的上下文缓存。
- 每天有预算上限，超了就不再编，Deck 上写明。
"""
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import prices

BJ = timezone(timedelta(hours=8))

# 元 / 百万 tokens（非高峰价；高峰 ×2）。来源：DeepSeek 8 月公布的分时定价（TechNode 转述），开工前以官方价格页为准，可在 config.json 的 news.price 覆盖。
DEFAULT_PRICE = {"hit": 0.05, "miss": 1.5, "out": 4.5}

COMPANIES = ["OpenAI", "Anthropic", "Google DeepMind", "Meta", "DeepSeek", "阿里通义"]
BEATS = ["脑机接口", "机器人", "芯片与材料", "航天与能源"]

# 报纸名字：config.news.title 可改（嫌「德克日报」怪，改名）
DEFAULT_TITLE = "Vesper 日报"
DEFAULT_TITLE_EN = "THE VESPER DAILY"

DEFAULT_INTERESTS = ("脑机接口、机器人（人形、灵巧手、家用）、芯片与新材料、航天与能源等前沿科技；"
                     "GitHub 上新火起来的开源软件和开源硬件，特别是能在 Mac 上玩的、和本地大模型/语音/键盘/小屏外设有关的；"
                     "几家 AI 巨头（OpenAI、Anthropic、Google DeepMind、Meta、DeepSeek、阿里通义）的新模型、新产品、价格变化；"
                     "艺术和代码结合（生成艺术、着色器、TouchDesigner、p5.js、AI 编程做创作）；"
                     "会影响他自选股的科技新闻。他用 Mac，装了一个叫 Deck 的本地控制台和语音助手。")

SYSTEM = """你是「{title}」的总编辑，给一位读者编一份个人科技日报，显示在他 Mac 旁边的小屏上。
他关心：{interests}

你会收到一批候选新闻，每条一行：id｜来源｜分栏提示｜标题｜摘要。只能从候选里选，不许编造候选里没有的事实、数字、公司动态；
拿不准的就不选。英文标题要译成自然的中文。标题不超过 28 个字，摘要两三句、不超过 90 个字，说清「发生了什么、为什么值得他看」。
他嫌讲得太浅：摘要里要有候选里给出的具体细节（谁、多少、用了什么方法、做到什么程度、下一步是什么），不要只写一句空泛的评价。
前沿科技（frontier）要讲得更细：lead 的 dek 写四五句、150 到 200 字；beats 里每条的 dek 写三四句、80 到 120 字。
不要用「重磅」「震惊」之类的词。同一件事多个来源报道时，选信息最全的那条 id，并在 sources 里列出其它 id。

只输出一个 JSON 对象，结构如下（数组可以比上限少，宁缺毋滥）。按这个顺序写：先写口播稿，再排版面。
{{
 "script": ["口播稿，每个元素一句话"],
 "script_ids": ["口播稿里展开讲了的候选 id"],
 "front": {{
   "lead": {{"id": "", "kicker": "分栏名", "title": "", "dek": "", "sources": []}},
   "briefs": [{{"id": "", "tag": "两个字分类", "text": "一句话，不超过 30 字"}}],   // 最多 5 条，按重要程度排；会放进页面顶上滚动的跑马灯，和 script_ids 最多重合 2 条
   "stories": [{{"id": "", "kicker": "", "title": "", "dek": ""}}]                 // 正好 3 条，和 lead 不重复
 }},
 "frontier": {{
   "lead": {{"id": "", "kicker": "", "title": "", "dek": ""}},   // 不能和 front.lead 是同一条
   "beats": [{{"beat": "脑机接口|机器人|芯片与材料|航天与能源", "items": [{{"id": "", "title": "", "dek": ""}}]}}],  // 每条线最多 2 条
   "papers": [{{"id": "", "text": "一句话说论文做了什么"}}]     // 最多 4 条
 }},
 "giants": [{{"company": "OpenAI|Anthropic|Google DeepMind|Meta|DeepSeek|阿里通义", "items": [{{"id": "", "title": ""}}]}}],  // 每家最多 3 条，没有就给空数组
 "giants_week": "一句话总结这两天巨头们的动向",
 "oss_why": {{"候选id": "一句话说为什么火、对他有什么用，不超过 30 字"}},   // 给 GitHub、开源硬件、HF 模型候选写，最多 18 条
 "picks": [{{"id": "", "why": "为什么他会想试，结合他在折腾的东西，不超过 40 字"}}],   // 最多 2 个开源项目
 "art": {{
   "lead": {{"id": "", "title": "", "dek": ""}},
   "items": [{{"id": "", "title": ""}}]       // 最多 6 条
 }},
 "invest": [{{"id": "", "symbols": ["NVDA.US"], "impact": "up|down|neutral", "title": "", "why": "为什么会影响这只股票，不超过 40 字"}}],  // 只用候选里分栏提示为 stock 的，最多 6 条
}}

口播稿（script）的要求：
- 口语、自然，用「你」称呼他；开头按用户消息里给的问候语问好，然后说「这是今天的{title}」。
- 一共 24 到 34 句，大约四五分钟。挑四到六件事讲透，每件事讲三到五句：发生了什么、关键细节和数字、为什么重要、和他有什么关系。前沿科技至少讲两件。
- 页面顶上有一条滚动的跑马灯，放的是 front.briefs。口播和跑马灯要互补，他上一期抱怨口播几乎就是把跑马灯念了一遍：
  先写口播、在 script_ids 里列出讲了哪些 id；再选 briefs 时优先挑口播没讲的事，和 script_ids 最多重合 2 条。重合的那两件，口播里要讲跑马灯标题以外的细节。
- 他在长桥只授权了自选，不知道他的持仓：说「你的自选」，不要说「你的持仓」「你持有的」。
- 不要念网址和英文缩写的全称；每句不超过 60 个字，方便逐句高亮。
- 股价：用户消息末尾有长桥自选的实时行情。提到自选股的现价、涨跌只能用那里的数字；新闻里的股价可能是几天前的，不要照抄，也不要自己估算。
  分析师目标价可以引用候选新闻，但要说清是目标价。没给行情时不要写任何具体股价。写错的价格会被程序整句删掉。
- 自选股的行情单独成句，不要硬接在别的新闻后面（例如不要把「月球基地」和「你的自选涨了多少」放进一句）。
- 不写没有信息量的句子，比如「这边细节太少，我不展开」：没东西可讲的栏目就直接跳过。
"""


def is_peak(now=None):
    n = (now or datetime.now(BJ)).astimezone(BJ)
    if n.weekday() >= 5:
        return False
    h = n.hour + n.minute / 60.0
    return 9 <= h < 12 or 14 <= h < 18


def cost_cny(usage, price=None, peak=False):
    p = dict(DEFAULT_PRICE, **(price or {}))
    hit = usage.get("prompt_cache_hit_tokens") or 0
    miss = usage.get("prompt_cache_miss_tokens")
    if miss is None:
        miss = max(0, (usage.get("prompt_tokens") or 0) - hit)
    out = usage.get("completion_tokens") or 0
    c = (hit * p["hit"] + miss * p["miss"] + out * p["out"]) / 1e6
    return round(c * (2 if peak else 1), 4)


def pack_candidates(items, limit=170):
    """候选压成一行一条，越短越省钱。每个分栏都留够名额，免得 HN 把别的挤掉。"""
    quota = {"general": 30, "giants": 40, "frontier": 30, "oss": 15, "hw": 10, "model": 10, "paper": 16, "art": 14, "stock": 20}
    used, rows, keep = {}, [], {}
    for it in sorted(items, key=lambda x: -(x["ts"] or 0)):
        h = it["hint"]
        if used.get(h, 0) >= quota.get(h, 10) or len(rows) >= limit:
            continue
        used[h] = used.get(h, 0) + 1
        extra = ""
        ex = it.get("extra") or {}
        if ex.get("stars_today"):
            extra = "（今日 +%s★）" % ex["stars_today"]
        elif ex.get("stars"):
            extra = "（%s★）" % ex["stars"]
        elif ex.get("symbols"):
            extra = "（相关：%s）" % ",".join(ex["symbols"])
        # 摘要给长一点模型才讲得细；开源/模型/论文只要一句，省钱
        snip = (it.get("snippet") or "")[:100 if h in ("oss", "hw", "model") else 240]
        line = "%s｜%s｜%s｜%s%s｜%s" % (it["id"], it["src"], h, it["title"], extra, snip)
        rows.append(line.replace("\n", " "))
        keep[it["id"]] = it
    return "\n".join(rows), keep


class EditorError(Exception):
    usage = None


def call_deepseek(key, cfg, system, user, max_tokens=8000, timeout=180):
    base = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    base = re.sub(r"/v1$", "", base)
    models = [cfg.get("model") or "deepseek-flash"]
    if cfg.get("fallback_model", "deepseek-chat") not in models:
        models.append(cfg.get("fallback_model", "deepseek-chat"))
    last = None
    for model in models:
        # 关掉「思考」：编报纸不需要推理，开着的话 8000 token 几乎全花在思考上，正文会被截断（11-07 Mac 实测）
        body = {"model": model, "temperature": 0.4, "max_tokens": max_tokens, "stream": False,
                "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                d = json.loads(r.read())
            ch = (d.get("choices") or [{}])[0]
            msg = (ch.get("message") or {}).get("content") or ""
            usage = d.get("usage") or {}
            try:
                return parse_json(msg), usage, model
            except EditorError as e:
                # 回答不完整也已经花了钱：把用量带出去记账
                e.usage = usage
                if ch.get("finish_reason") == "length":
                    e.args = ("DeepSeek 写到一半被截断了（输出上限 %d）" % max_tokens,)
                raise
        except urllib.error.HTTPError as e:
            last = "DeepSeek 返回 %s" % e.code
            if e.code in (400, 404, 422):  # 模型名不认识之类：换下一个模型名再试
                continue
            raise EditorError(last)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise EditorError("连不上 DeepSeek：%s" % e)
    raise EditorError(last or "DeepSeek 调用失败")


def parse_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        d = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise EditorError("DeepSeek 没按格式回")
        try:
            d = json.loads(m.group(0))
        except ValueError:
            raise EditorError("DeepSeek 回的 JSON 不完整")
    if not isinstance(d, dict):
        raise EditorError("DeepSeek 回的不是对象")
    return d


# ---------------- 把编辑结果和原始候选合在一起（链接、时间都用原始的，不信模型给的） ----------------

def _s(v, n):
    v = "" if v is None else str(v)
    v = re.sub(r"\s+", " ", v).strip()
    return v if len(v) <= n else v[: n - 1] + "…"


def _story(o, keep, n_title=40, n_dek=140):
    if not isinstance(o, dict):
        return None
    it = keep.get(str(o.get("id") or ""))
    if not it:
        return None
    srcs = [keep[i]["src"] for i in (o.get("sources") or []) if isinstance(i, str) and i in keep]
    return {"id": it["id"], "url": it["url"], "src": it["src"], "ts": it["ts"],
            "kicker": _s(o.get("kicker") or o.get("tag") or "", 12),
            "title": _s(o.get("title") or o.get("text") or it["title"], n_title),
            "dek": _s(o.get("dek") or o.get("why") or "", n_dek),
            "n_src": 1 + len(set(srcs) - {it["src"]})}


def _list(v):
    return v if isinstance(v, list) else []


def assemble(ed, keep, raw_items):
    """返回前端直接能用的一期。模型给错的 id 一律丢掉。"""
    front = ed.get("front") or {}
    briefs = []
    said = set(str(i) for i in _list(ed.get("script_ids")))
    overlap = 0
    for x in _list(front.get("briefs"))[:5]:
        if isinstance(x, dict) and str(x.get("id")) in said:
            overlap += 1
            if overlap > 2:  # 跑马灯和口播最多重合 2 件，多的不放进跑马灯
                continue
        b = _story(x, keep, 36, 0)
        if b:
            b["kicker"] = _s(x.get("tag") or "", 6)
            briefs.append(b)
    out = {"front": {"lead": _story(front.get("lead"), keep, 40, 160), "briefs": briefs,
                     "stories": [b for b in (_story(x, keep) for x in _list(front.get("stories"))[:3]) if b]}}
    fr = ed.get("frontier") or {}
    beats = []
    for name in BEATS:
        bx = next((x for x in _list(fr.get("beats")) if isinstance(x, dict) and x.get("beat") == name), {})
        beats.append({"beat": name, "items": [s for s in (_story(i, keep, 40, 140) for i in _list(bx.get("items"))[:2]) if s]})
    fl = _story(fr.get("lead"), keep, 40, 220)
    fl_id = (fl or {}).get("id")
    if fl_id and fl_id == ((out["front"]["lead"] or {}).get("id")):
        # 前沿主稿和头版头条撞了：从各条线里提一条上来当主稿，免得两页看到同一篇
        fl = None
        for b in beats:
            if b["items"]:
                fl = b["items"].pop(0)
                break
    out["frontier"] = {"lead": fl, "beats": beats,
                       "papers": [s for s in (_story(dict(p, title=p.get("text")) if isinstance(p, dict) else None, keep, 40, 0)
                                              for p in _list(fr.get("papers"))[:4]) if s]}
    giants = []
    for co in COMPANIES:
        gx = next((x for x in _list(ed.get("giants")) if isinstance(x, dict) and x.get("company") == co), {})
        giants.append({"company": co, "items": [s for s in (_story(i, keep, 40, 0) for i in _list(gx.get("items"))[:3]) if s]})
    out["giants"] = giants
    out["giants_week"] = _s(ed.get("giants_week") or "", 60)
    why = ed.get("oss_why") if isinstance(ed.get("oss_why"), dict) else {}
    oss = {"sw": [], "hw": [], "md": []}
    for it in raw_items:
        k = {"oss": "sw", "hw": "hw", "model": "md"}.get(it["hint"])
        if not k:
            continue
        ex = it.get("extra") or {}
        oss[k].append({"id": it["id"], "repo": ex.get("repo") or it["title"], "url": it["url"], "lang": ex.get("lang") or "",
                       "desc": _s(it.get("snippet"), 80), "why": _s(why.get(it["id"]) or "", 40),
                       "stars_today": ex.get("stars_today") or 0, "stars": ex.get("stars") or 0,
                       "downloads": ex.get("downloads") or 0, "likes": ex.get("likes") or 0})
    oss["sw"].sort(key=lambda r: (-(r["stars_today"] or 0), -(r["stars"] or 0)))
    oss["hw"].sort(key=lambda r: -(r["stars"] or 0))
    for k in oss:
        oss[k] = oss[k][:8]
    out["oss"] = oss
    out["picks"] = [s for s in (_story(p, keep, 40, 60) for p in _list(ed.get("picks"))[:2]) if s]
    art = ed.get("art") or {}
    out["art"] = {"lead": _story(art.get("lead"), keep, 40, 140),
                  "items": [s for s in (_story(i, keep, 30, 0) for i in _list(art.get("items"))[:6]) if s]}
    out["invest"] = []
    for x in _list(ed.get("invest"))[:6]:
        s = _story(x, keep, 36, 60)
        if not s:
            continue
        s["symbols"] = [_s(v, 12) for v in _list(x.get("symbols"))[:3] if isinstance(v, str)]
        s["impact"] = x.get("impact") if x.get("impact") in ("up", "down", "neutral") else "neutral"
        out["invest"].append(s)
    out["script"] = [_s(l, 120) for l in _list(ed.get("script"))[:40] if isinstance(l, str) and l.strip()]
    return out


def edit(items, key, cfg, log=print, market=None):
    """跑一次编辑。返回 (edition_body, meta)。market 是长桥快照（market.json），用来给编辑报价、编完核对股价。"""
    snap = prices.snapshot(market)
    interests = cfg.get("interests") or DEFAULT_INTERESTS
    system = SYSTEM.format(interests=interests, title=cfg.get("title") or DEFAULT_TITLE)
    lines, keep = pack_candidates(items, int(cfg.get("max_candidates", 170)))
    if len(keep) < 8:
        raise EditorError("今天抓到的新闻太少（%d 条），先不编" % len(keep))
    now = datetime.now(BJ)
    hello = "早上好" if now.hour < 11 else "中午好" if now.hour < 13 else "下午好" if now.hour < 18 else "晚上好"
    user = "现在是 %s（北京时间，口播稿开头说「%s」）。候选共 %d 条：\n%s" % (
        now.strftime("%Y年%m月%d日 %A %H:%M"), hello, len(keep), lines)
    user += "\n\n" + prices.prompt_block(snap, (market or {}).get("updated"))
    t0 = time.time()
    ed, usage, model = call_deepseek(key, cfg, system, user)
    body = assemble(ed, keep, items)
    fixes = prices.check_edition(body, snap)
    for f in fixes:
        log("[news] 股价核对：" + f)
    peak = is_peak(now)
    meta = {"model": model, "usage": usage, "cost": cost_cny(usage, cfg.get("price"), peak), "peak": peak,
            "seconds": round(time.time() - t0, 1), "candidates": len(keep)}
    if fixes:
        meta["price_fixes"] = fixes[:20]
    return body, meta


INVEST_SYSTEM = """你帮用户看自选股相关的新闻。给你一批新闻（id｜相关股票｜标题｜摘要），挑出真会影响股价的，最多 6 条。
只输出 JSON：{"invest": [{"id": "", "symbols": ["NVDA.US"], "impact": "up|down|neutral", "title": "中文标题，不超过 28 字", "why": "为什么会影响，不超过 40 字"}]}
不许编造新闻里没有的事实和数字；拿不准影响方向就写 neutral。
这些股票是他的自选，不代表他持有：说「你的自选」，不要说「你的持仓」「你持有的」。"""


def tag_invest(stock_items, key, cfg, market=None):
    lines, keep = pack_candidates(stock_items, 40)
    if not keep:
        return [], {}
    ed, usage, model = call_deepseek(key, cfg, INVEST_SYSTEM, lines, max_tokens=1500, timeout=90)
    out = assemble({"invest": ed.get("invest")}, keep, [])["invest"]
    prices.check_edition({"invest": out}, prices.snapshot(market))
    return out, {"model": model, "usage": usage, "cost": cost_cny(usage, cfg.get("price"), is_peak())}
