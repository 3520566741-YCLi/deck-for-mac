"""资讯页的免费来源。这里一分钱都不花：只用公开接口和 RSS，不调 DeepSeek。

每个来源返回一组「候选」：
  {"id", "src", "hint", "title", "url", "snippet", "ts", "extra"}
hint 是给编辑看的分栏提示（frontier / giants / oss / hw / model / art / paper / general）。
任何一个来源失败都只记一条错误，不影响别的来源。只用标准库，兼容 macOS 自带 python3 (3.9)。
"""
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

UA = "Mozilla/5.0 (Macintosh) DeckNews/1.0 (+local news digest)"
MAX_BYTES = 3 * 1024 * 1024  # 单个来源最多读 3MB，防止异常大的响应拖垮内存

# 默认 RSS：(名字, 地址, 分栏提示)。config.json 的 news.feeds 可以整组替换。
DEFAULT_FEEDS = [
    ["OpenAI", "https://openai.com/news/rss.xml", "giants"],
    ["Google DeepMind", "https://deepmind.google/blog/rss.xml", "giants"],
    ["Google AI", "https://blog.google/technology/ai/rss/", "giants"],
    ["TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", "giants"],
    ["IEEE Spectrum 机器人", "https://spectrum.ieee.org/feeds/topic/robotics.rss", "frontier"],
    ["IEEE Spectrum 生物医学", "https://spectrum.ieee.org/feeds/topic/biomedical.rss", "frontier"],
    ["Hackaday", "https://hackaday.com/blog/feed/", "hw"],
    ["CreativeApplications", "https://www.creativeapplications.net/feed/", "art"],
    # 36氪 的 /feed 现在返回反爬验证页（不是 RSS），换成 IT之家、量子位；36氪 走下面的 Google 新闻站内搜索
    ["IT之家", "https://www.ithome.com/rss/", "general"],
    ["量子位", "https://www.qbitai.com/feed", "general"],
]

# 没有 RSS 的公司和话题：用 Google 新闻搜索的 RSS（免费，不用 key）。
DEFAULT_SEARCHES = [
    ["Anthropic", "Anthropic Claude", "en", "giants"],
    ["Meta AI", "Meta AI Llama", "en", "giants"],
    ["xAI", "xAI Grok", "en", "giants"],
    ["DeepSeek", "DeepSeek", "zh", "giants"],
    ["通义", "通义千问 OR Qwen 阿里", "zh", "giants"],
    ["脑机接口", "脑机接口", "zh", "frontier"],
    ["BCI", "brain-computer interface", "en", "frontier"],
    ["人形机器人", "人形机器人", "zh", "frontier"],
    ["Humanoid", "humanoid robot", "en", "frontier"],
    ["生成艺术", "generative art OR creative coding OR shader art", "en", "art"],
    ["36氪", "site:36kr.com AI OR 机器人 OR 芯片", "zh", "general"],
]


_GH = {"token": None, "at": 0.0}


def gh_headers():
    """有 gh 登录就带上 token（每小时 5000 次）；没有就匿名（60 次，容易被限）。token 只放在请求头里，不落盘不打印。"""
    import os
    import shutil
    import subprocess
    if time.time() - _GH["at"] > 3600:
        _GH["at"] = time.time()
        tok = os.environ.get("GITHUB_TOKEN") or ""
        exe = shutil.which("gh") or next((p for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh") if os.path.exists(p)), None)
        if not tok and exe:
            try:
                tok = subprocess.run([exe, "auth", "token"], capture_output=True, text=True, timeout=8).stdout.strip()
            except Exception:  # noqa: BLE001
                tok = ""
        _GH["token"] = tok or None
    h = {"Accept": "application/vnd.github+json"}
    if _GH["token"]:
        h["Authorization"] = "Bearer " + _GH["token"]
    return h


def _get(url, timeout=15, headers=None):
    req = urllib.request.Request(url, headers=dict({"User-Agent": UA, "Accept": "*/*"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 （只访问上面写死或配置里的 https 地址）
        return r.read(MAX_BYTES + 1)[:MAX_BYTES]


def _json(url, timeout=15, headers=None):
    return json.loads(_get(url, timeout, headers) or b"null")


def clean(text, n=240):
    """去掉 HTML 标签和多余空白，截断到 n 个字。"""
    if not text:
        return ""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", str(text), flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _id(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


def _ts(s):
    """各种日期字符串 → unix 秒；认不出就返回 0。"""
    if not s:
        return 0
    if isinstance(s, (int, float)):
        return int(s)
    s = str(s).strip()
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def safe_url(u):
    """只放行 http(s) 链接，别的（javascript: 之类）一律丢掉。"""
    u = (u or "").strip()
    return u if re.match(r"^https?://", u, re.I) else ""


def item(src, hint, title, url, snippet="", ts=0, **extra):
    title = clean(title, 200)
    url = safe_url(url)
    if not title or not url:
        return None
    return {"id": _id(src, url or title), "src": src, "hint": hint, "title": title, "url": url,
            "snippet": clean(snippet), "ts": int(ts or 0), "extra": extra}


# ---------------- 各个来源 ----------------

def hacker_news(limit=30):
    d = _json("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=%d" % limit)
    out = []
    for h in (d or {}).get("hits", []):
        url = h.get("url") or "https://news.ycombinator.com/item?id=%s" % h.get("objectID")
        out.append(item("Hacker News", "general", h.get("title"), url, "", h.get("created_at_i"),
                        points=h.get("points"), comments=h.get("num_comments")))
    return [x for x in out if x]


def _int(t):
    try:
        return int(re.sub(r"[^\d]", "", t or "") or 0)
    except ValueError:
        return 0


def github_trending(limit=15):
    """GitHub Trending 日榜页（https://github.com/trending）：有「今日新增星」。
    OSS Insight 的 24 小时趋势 2026-03 起停了（返回空），所以改抓这个页面；页面改版抓不到时退回 GitHub 搜索「最近一周新建、星最多」。"""
    out = []
    try:
        page = _get("https://github.com/trending?since=daily", timeout=20, headers={"Accept": "text/html"}).decode("utf-8", "replace")
        for art in re.findall(r'<article class="Box-row".*?</article>', page, re.S)[:limit]:
            m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([\w.-]+/[\w.-]+)"', art, re.S)
            if not m:
                continue
            name = m.group(1)
            desc = re.search(r'<p class="[^"]*color-fg-muted[^"]*"[^>]*>(.*?)</p>', art, re.S)
            lang = re.search(r'itemprop="programmingLanguage"[^>]*>(.*?)</span>', art, re.S)
            total = re.search(r'href="/%s/stargazers"[^>]*>(.*?)</a>' % re.escape(name), art, re.S)
            today = re.search(r'([\d,]+)\s+stars?\s+(?:today|this)', art)
            it = item("GitHub 趋势", "oss", name, "https://github.com/" + name, clean(desc.group(1)) if desc else "", 0,
                      repo=name, lang=clean(lang.group(1)) if lang else "",
                      stars_today=_int(today.group(1)) if today else 0, stars=_int(clean(total.group(1))) if total else 0)
            if it:
                out.append(it)
    except Exception as e:  # noqa: BLE001
        print("[news] GitHub Trending 页面抓不到，退回 GitHub 搜索：%s" % e, flush=True)
        out = []
    if out:
        return out
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    return _gh_search("created:>%s" % since, "oss", limit)


def _gh_search(q, hint, limit):
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {"q": q, "sort": "stars", "order": "desc", "per_page": limit})
    d = _json(url, headers=gh_headers())
    out = []
    for r in (d or {}).get("items", []):
        it = item("GitHub", hint, r.get("full_name"), r.get("html_url"), r.get("description"), _ts(r.get("created_at")),
                  repo=r.get("full_name"), lang=r.get("language") or "", stars=r.get("stargazers_count") or 0)
        if it:
            out.append(it)
    return out


def github_hardware(limit=8):
    """开源硬件：最近一个月新建、星最多的硬件 / 机器人 / PCB 项目。"""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    seen, out = set(), []
    for topic in ("open-source-hardware", "robotics", "pcb", "keyboard"):
        try:
            for it in _gh_search("topic:%s created:>%s" % (topic, since), "hw", limit):
                if it["id"] not in seen:
                    seen.add(it["id"])
                    out.append(it)
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda x: -(x["extra"].get("stars") or 0))
    return out[: limit + 4]


def hf_models(limit=12):
    d = _json("https://huggingface.co/api/models?sort=trendingScore&limit=%d" % limit)
    out = []
    for m in d or []:
        mid = m.get("id") or m.get("modelId")
        it = item("Hugging Face", "model", mid, "https://huggingface.co/" + (mid or ""), m.get("pipeline_tag") or "",
                  _ts(m.get("createdAt")), repo=mid, likes=m.get("likes") or 0, downloads=m.get("downloads") or 0,
                  trending=m.get("trendingScore") or 0)
        if it:
            out.append(it)
    return out


def hf_papers(limit=12):
    d = _json("https://huggingface.co/api/daily_papers?limit=%d" % limit)
    out = []
    for p in d or []:
        pp = p.get("paper") or {}
        pid = pp.get("id") or ""
        it = item("HF 每日论文", "paper", pp.get("title") or p.get("title"), "https://huggingface.co/papers/" + pid,
                  pp.get("summary") or p.get("summary"), _ts(p.get("publishedAt") or pp.get("publishedAt")),
                  upvotes=pp.get("upvotes") or 0)
        if it:
            out.append(it)
    return out


ATOM = "{http://www.w3.org/2005/Atom}"


def arxiv(cats=("cs.RO", "q-bio.NC", "eess.SP"), limit=12):
    q = " OR ".join("cat:%s" % c for c in cats)
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": q, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": limit})
    raw = _get(url, timeout=20)
    if b"<!ENTITY" in raw.upper():
        raise ValueError("arXiv 返回了自定义实体，跳过")
    root = ET.fromstring(raw)
    out = []
    for e in root.findall(ATOM + "entry"):
        it = item("arXiv", "paper", e.findtext(ATOM + "title"), e.findtext(ATOM + "id"),
                  e.findtext(ATOM + "summary"), _ts(e.findtext(ATOM + "published")))
        if it:
            out.append(it)
    return out


def parse_feed(raw, name, hint, limit=12):
    """RSS 2.0 和 Atom 都认。正常的订阅源不会自定义实体：带 <!ENTITY 的一律不解析（防「实体膨胀」把内存撑爆）。"""
    if b"<!ENTITY" in raw.upper():
        raise ValueError("订阅源里有自定义实体，跳过")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # 有的源（比如 36氪）夹着控制字符或没转义的 &：清一下再试一次
        txt = raw.decode("utf-8", "replace")
        txt = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", txt)
        txt = re.sub(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]*;)", "&amp;", txt)
        try:
            root = ET.fromstring(txt.encode("utf-8"))
        except ET.ParseError:
            # 还是坏的（比如正文里有没转义的 < 或 HTML 实体）：不当 XML 解析了，按 <item> 块用正则逐条抠
            return _loose_items(txt, name, hint, limit)
    out = []
    entries = root.findall(".//item")
    if entries:
        for e in entries[:limit]:
            it = item(name, hint, e.findtext("title"), e.findtext("link"),
                      e.findtext("description") or "", _ts(e.findtext("pubDate") or e.findtext("{http://purl.org/dc/elements/1.1/}date")))
            if it:
                out.append(it)
        return out
    for e in root.findall(ATOM + "entry")[:limit]:
        link = ""
        for l in e.findall(ATOM + "link"):
            if l.get("rel") in (None, "alternate"):
                link = l.get("href")
                break
        it = item(name, hint, e.findtext(ATOM + "title"), link,
                  e.findtext(ATOM + "summary") or e.findtext(ATOM + "content") or "",
                  _ts(e.findtext(ATOM + "updated") or e.findtext(ATOM + "published")))
        if it:
            out.append(it)
    return out


def _loose_items(txt, name, hint, limit):
    def tag(block, t):
        m = re.search(r"<%s(?:\s[^>]*)?>(.*?)</%s>" % (t, t), block, re.S | re.I)
        if not m:
            return ""
        v = m.group(1).strip()
        c = re.match(r"<!\[CDATA\[(.*?)\]\]>$", v, re.S)
        return html.unescape(c.group(1) if c else v)

    out = []
    for block in re.findall(r"<item(?:\s[^>]*)?>(.*?)</item>", txt, re.S | re.I)[:limit]:
        it = item(name, hint, clean(tag(block, "title"), 200), tag(block, "link").strip(), tag(block, "description"),
                  _ts(tag(block, "pubDate") or tag(block, "dc:date")))
        if it:
            out.append(it)
    if not out:
        raise ValueError("订阅源格式坏了，正则也没抠出条目")
    return out


def rss(name, url, hint, limit=12):
    return parse_feed(_get(url), name, hint, limit)


def google_news(name, query, lang, hint, limit=8):
    if lang == "zh":
        params = {"q": query + " when:2d", "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}
    else:
        params = {"q": query + " when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"}
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)
    out = parse_feed(_get(url), name, hint, limit)
    for it in out:
        it["snippet"] = ""  # Google 新闻的 description 只是一串链接，没用
    return out


# ---------------- 汇总 ----------------

def gather(cfg=None, log=print):
    """把所有来源抓一遍，返回 (candidates, report)。report 记每个来源抓到几条、有没有出错。"""
    cfg = cfg or {}
    jobs = [("Hacker News", hacker_news, ()), ("GitHub 趋势", github_trending, ()),
            ("开源硬件", github_hardware, ()), ("HF 模型", hf_models, ()), ("HF 论文", hf_papers, ()),
            ("arXiv", arxiv, ())]
    for f in cfg.get("feeds") or DEFAULT_FEEDS:
        jobs.append((f[0], rss, tuple(f)))
    for s in cfg.get("searches") or DEFAULT_SEARCHES:
        jobs.append(("搜索·" + s[0], google_news, tuple(s)))
    # 并发抓，单个慢源不拖住整期
    import concurrent.futures as cf
    items, report = [], []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fn, *args): name for name, fn, args in jobs}
        for fut in cf.as_completed(futs):
            name = futs[fut]
            try:
                got = fut.result() or []
                items.extend(got)
                report.append({"src": name, "n": len(got)})
            except Exception as e:  # noqa: BLE001
                report.append({"src": name, "n": 0, "error": str(e)[:120]})
                log("[news] %s 抓取失败：%s" % (name, e))
    report.sort(key=lambda r: r["src"])
    return dedupe(items), report


def _norm(t):
    return re.sub(r"[\W_]+", "", (t or "").lower())[:60]


def dedupe(items, max_age_days=4):
    """同一链接或几乎一样的标题只留一条；太旧的（有日期且超过 4 天）丢掉。"""
    now = time.time()
    seen_id, seen_t, out = set(), set(), []
    for it in items:
        if it["ts"] and now - it["ts"] > max_age_days * 86400 and it["hint"] not in ("oss", "hw", "model"):
            continue
        k = _norm(it["title"])
        if it["id"] in seen_id or (k and k in seen_t):
            continue
        seen_id.add(it["id"])
        seen_t.add(k)
        out.append(it)
    return out
