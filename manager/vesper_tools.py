"""Vesper 能操作的 Deck 功能（11-08 补齐）：资讯 / 日报、行情、进程、磁盘、GitHub、我的应用、工具箱、设置、显示方式、每日清理。

原则（和 assistant.py 一致）：
- 只读的直接做；会改东西的（退出 / 结束进程、卸载、清垃圾）先返回 needs_confirm，屏幕上出一个提示，
  只有他最新一句话明确同意才由 confirm_action 执行；东西只进废纸篓、能撤销。
- 页面上的动作（播放日报、改设置、切显示方式）通过 {"t": "ui"} 事件交给页面做，跟 open_card 一样。
- 绝不调用长桥交易接口：行情只读 NewsRoom 已经拿到的快照。
- 只用标准库。
"""
import json
import os
import re
import time
import uuid

SECTIONS = {"front": "头版", "frontier": "前沿科技", "giants": "AI 巨头", "oss": "开源热榜", "art": "艺术 × 代码", "invest": "投资"}
SETTINGS = {
    "skin": {"soft": "新拟态", "glass": "玻璃"},
    "mode": {"fit": "一屏看完", "scroll": "可滚动"},
    "notchSize": {"s": "小", "m": "中", "l": "大"},
    "notchLayout": {"scroll": "上下滑", "pages": "左右翻页"},
    "glass": {"70": "70%", "60": "60%", "50": "50%", "40": "40%", "30": "30%"},
    "sfx": {"on": "开", "off": "关"},
    "sfxVol": {"s": "轻", "m": "中", "l": "响"},
    "reduce": {"on": "开", "off": "关"},
}
SETTING_NAMES = {"skin": "皮肤", "mode": "排版", "notchSize": "刘海面板大小", "notchLayout": "刘海里卡片怎么排",
                 "glass": "Vesper 玻璃透明度", "sfx": "按键音效", "sfxVol": "按键音量", "reduce": "减少动画"}
OP_TTL = 10 * 60

TOOLS = [
    {"name": "news_read", "description": "读资讯页（Vesper 日报）当前这一期的内容。section：overview=概要（期号、时间、各栏标题），front=头版，frontier=前沿科技，giants=AI 巨头，oss=开源热榜，art=艺术 × 代码，invest=投资（自选股相关新闻），script=日报口播稿全文。他问「今天有什么新闻 / 日报说了什么 / 前沿科技讲了啥」时用。",
     "parameters": {"type": "object", "properties": {"section": {"type": "string", "enum": ["overview", "front", "frontier", "giants", "oss", "art", "invest", "script"]}}, "required": ["section"]}},
    {"name": "news_brief", "description": "控制资讯页上的「Vesper 日报」语音播放器（会自动切到资讯页）。play=从头播，resume=从暂停的地方接着播，pause=暂停，restart=从头再念，next/prev=跳到下一句/上一句。他说「读一下今天的日报 / 播日报 / 念一下早报」就用 play。",
     "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["play", "resume", "pause", "restart", "next", "prev"]}}, "required": ["action"]}},
    {"name": "news_refresh", "description": "马上重编一期资讯（花 DeepSeek 的钱，受每天预算限制，大约要 1～3 分钟）。只有他明确要「重新出一期 / 更新一下资讯」时用。", "parameters": {"type": "object", "properties": {}}},
    {"name": "market_quotes", "description": "读资讯页投资栏的行情（长桥只读快照）：自选股现价和涨跌、几个指数、市场温度。可以只问某只（symbol 填代码或名字，如 NVDA、腾讯）。只读，不能交易。",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}}},
    {"name": "top_processes", "description": "看现在最占 CPU / 内存 / GPU 的进程（和点开监控台圆环看到的一样）。",
     "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["cpu", "mem", "gpu"]}, "n": {"type": "integer"}}, "required": ["kind"]}},
    {"name": "process_quit", "description": "结束一个进程（先用 top_processes 拿到 pid 和 name）。会先在屏幕上出确认，他同意后再用 confirm_action 执行。系统进程和 Deck 自己要用的进程不能结束。",
     "parameters": {"type": "object", "properties": {"pid": {"type": "integer"}, "name": {"type": "string"}, "force": {"type": "boolean"}}, "required": ["pid", "name"]}},
    {"name": "disk_usage", "description": "磁盘面板：overview=各个盘剩多少、今天少了多少；grow=谁在变大（window: hour/day/week）；recent=最近新增的大文件；writers=现在谁在写硬盘。vol 不填就是 Mac 内置盘。只读。",
     "parameters": {"type": "object", "properties": {"view": {"type": "string", "enum": ["overview", "grow", "recent", "writers"]}, "window": {"type": "string", "enum": ["hour", "day", "week"]}, "vol": {"type": "string"}}, "required": ["view"]}},
    {"name": "github_repo", "description": "看 GitHub 卡片里某个仓库的明细：最近提交、开着的 PR、Actions 运行、访客来源。repo 填仓库名（可以只写名字的一部分）。只读。",
     "parameters": {"type": "object", "properties": {"repo": {"type": "string"}}, "required": ["repo"]}},
    {"name": "app_control", "description": "对一个应用：open 打开、quit 退出、restart 重启、reveal 在访达里显示。「我的应用」卡片里登记的应用和 /Applications 里的应用都行。quit / restart 要先确认（confirm_action）。",
     "parameters": {"type": "object", "properties": {"app": {"type": "string"}, "action": {"type": "string", "enum": ["open", "quit", "restart", "reveal"]}}, "required": ["app", "action"]}},
    {"name": "toolbox_scan", "description": "工具箱的扫描（只看不动）：space=家目录各文件夹多大，junk=能清的垃圾（缓存、日志、旧安装包等），files=大文件和重复文件，updates=哪些应用有更新，apps=装了哪些应用。扫描要几秒到一分钟。",
     "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["space", "junk", "files", "updates", "apps"]}}, "required": ["kind"]}},
    {"name": "junk_clean", "description": "把 toolbox_scan(junk) 扫到的垃圾移到废纸篓（rules 不填就是默认勾选的那些）。要先确认（confirm_action），能撤销。",
     "parameters": {"type": "object", "properties": {"plan_id": {"type": "string"}, "rules": {"type": "array", "items": {"type": "string"}}}, "required": ["plan_id"]}},
    {"name": "uninstall_app", "description": "卸载一个应用：把应用本体和找到的残留（默认勾选的）移到废纸篓。要先确认（confirm_action），能撤销。系统应用和 Deck 自己的应用不能卸。",
     "parameters": {"type": "object", "properties": {"app": {"type": "string"}}, "required": ["app"]}},
    {"name": "deck_setting", "description": "改 Deck 设置（直接做）：skin 皮肤 soft/glass；mode 排版 fit/scroll；notchSize 刘海面板 s/m/l；notchLayout 刘海排法 scroll/pages；glass 玻璃透明度 70/60/50/40/30；sfx 按键音效 on/off；sfxVol 音量 s/m/l；reduce 减少动画 on/off；card 显示或隐藏某张卡片（card 填卡片 key，value 填 show/hide）。深浅色用 set_theme。",
     "parameters": {"type": "object", "properties": {"name": {"type": "string", "enum": ["skin", "mode", "notchSize", "notchLayout", "glass", "sfx", "sfxVol", "reduce", "card"]},
                                                     "value": {"type": "string"}, "card": {"type": "string"}}, "required": ["name", "value"]}},
    {"name": "display_mode", "description": "切换 Deck 的显示方式：auto=插着 iPad 用扩展屏、拔掉从刘海弹出；ext=一直在扩展屏；notch=从刘海弹出。",
     "parameters": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["auto", "ext", "notch"]}}, "required": ["mode"]}},
    {"name": "daily_cleanup", "description": "每日清理：status=最近几次清了什么、下次什么时候；auto_on=每晚 23:30 核对完直接移进废纸篓；auto_off=每晚先问他；undo=把某次清理的东西放回原处（run_id 从 status 里拿）。",
     "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["status", "auto_on", "auto_off", "undo"]}, "run_id": {"type": "string"}}, "required": ["action"]}},
    {"name": "confirm_action", "description": "他明确同意后，执行一个等确认的操作（process_quit / app_control 的退出重启 / junk_clean / uninstall_app 返回的 id）。只有他最新一句话是同意时才能调用。",
     "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
]
NAMES = {t["name"] for t in TOOLS}

PROMPT = """
## Deck 里的其他功能（都能直接用）
- 资讯页 / Vesper 日报：他问新闻、日报、前沿科技、巨头、开源热榜、艺术、投资新闻，用 news_read 读这一期对应的栏目再回答（别说没有）。他要「读 / 播 / 念一下日报」就调用 news_brief(play)，日报会在资讯页用 Vesper 的声音播出来、逐句高亮；这时你的回答只要一句很短的话（如「好，开始播今天的日报。」），别自己把日报内容再念一遍。「暂停 / 停一下」用 pause，「继续」用 resume，「从头读」用 restart，「下一条 / 跳过」用 next。他想要摘要或者只问某一栏，就用 news_read 自己讲重点，不用播放器。
- 行情：market_quotes（只读快照，绝不能下单；被要求交易就说做不了）。说涨跌时说百分比，别念一堆小数。
- 进程：top_processes 看谁最占 CPU / 内存 / GPU；要结束某个进程用 process_quit，先确认。
- 磁盘：disk_usage（剩多少、谁在变大、最近新增、谁在写硬盘）。
- GitHub：github_repo 看某个仓库的提交、PR、Actions、访客。
- 应用：app_control 打开 / 退出 / 重启 / 在访达里显示；卸载用 uninstall_app。
- 工具箱：toolbox_scan 看空间分布、垃圾、大文件、重复文件、更新、装了哪些应用；清垃圾用 junk_clean。
- 设置：deck_setting 改皮肤、排版、刘海面板、玻璃透明度、按键音效、减少动画、显示或隐藏卡片；display_mode 切显示方式（自动 / 扩展屏 / 刘海）；set_theme 换深浅色。
- 每日清理：daily_cleanup 查结果、开关自动清理、把清掉的东西放回去。
- 会改动东西的（结束进程、退出 / 重启应用、卸载、清垃圾）工具会先返回 needs_confirm：用一句话说清要做什么，问他行不行，他明确同意才调用 confirm_action(id)；犹豫或没回应就不做。
- 做不了的：iPad 局域网访问的开关、声纹录入（要他本人在设置 → 助手里录）、长桥授权，这些让他在屏幕上自己点。"""


def _pct(last, prev):
    try:
        return round((float(last) - float(prev)) / float(prev) * 100, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _story(s, dek=True):
    if not isinstance(s, dict):
        return None
    out = {"title": s.get("title"), "src": s.get("src")}
    if s.get("kicker"):
        out["tag"] = s["kicker"]
    if dek and s.get("dek"):
        out["dek"] = s["dek"]
    return out


def _clip(v, depth=0):
    """结果太长时压一压：列表留前 12 项，长字符串截断。"""
    if isinstance(v, dict):
        return {k: _clip(x, depth + 1) for k, x in v.items()} if depth < 5 else "{…}"
    if isinstance(v, list):
        return [_clip(x, depth + 1) for x in v[:12]] + (["…共 %d 项" % len(v)] if len(v) > 12 else [])
    if isinstance(v, str) and len(v) > 300:
        return v[:300] + "…"
    return v


def _hm(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else None


SYS_APP_DIRS = ["/Applications", "/Applications/Utilities", "/System/Applications", "/System/Applications/Utilities",
                os.path.expanduser("~/Applications")]
NO_QUIT = {"Finder", "Deck", "DeckVoice", "DeckHands", "Fusion Workbench", "Mac 管家", "loginwindow", "Dock", "SystemUIServer"}
_APPS = {"at": 0, "rows": []}


def _norm(s):
    return str(s or "").lower().replace(" ", "").replace("-", "").replace("_", "")


def launchable_apps():
    """能打开 / 退出的应用：/Applications、系统应用（/System/Applications，比如备忘录、日历）和 ~/Applications。
    名字收英文文件名、Info.plist 里的显示名和 Spotlight 的本地化显示名（「备忘录」），10 分钟缓存一次。"""
    if time.time() - _APPS["at"] < 600 and _APPS["rows"]:
        return _APPS["rows"]
    import apptools
    paths = []
    for d in SYS_APP_DIRS:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            if n.endswith(".app") and not n.startswith("."):
                paths.append(os.path.join(d, n))
    local = {}
    if paths:
        code, out, _ = apptools._run(["mdls", "-name", "kMDItemDisplayName", "-raw", "-nullMarker", "-"] + paths, 30)
        if code == 0:
            for p, v in zip(paths, out.split("\x00")):
                local[p] = v.strip()
    rows, seen = [], set()
    for p in paths:
        info = apptools._plist(p)
        bid = str(info.get("CFBundleIdentifier") or "")
        if not bid or bid in seen:
            continue
        seen.add(bid)
        base = os.path.basename(p)[:-4]
        names = {base, str(info.get("CFBundleDisplayName") or ""), str(info.get("CFBundleName") or ""), local.get(p, "")}
        names = [n[:-4] if n.endswith(".app") else n for n in names if n and n != "-"]
        rows.append({"path": p, "name": local.get(p) if local.get(p) not in (None, "", "-") else base, "base": base, "bid": bid,
                     "keys": sorted({_norm(n) for n in names})})
    _APPS.update(at=time.time(), rows=rows)
    return rows


def find_launchable(q):
    k = _norm(q)
    if not k:
        return None, []
    rows = launchable_apps()
    exact = [r for r in rows if k in r["keys"]]
    if exact:
        return exact[0], []
    part = [r for r in rows if any(k in x or (len(x) >= 2 and x in k) for x in r["keys"])]
    if len(part) == 1:
        return part[0], []
    return None, [r["name"] for r in part[:8]]


class Tools:
    def __init__(self, friday):
        self.f = friday
        self.ops = {}   # 等确认的操作 id -> {at, what, run}
        self.ext = {}   # server.py 装上：news（NewsRoom）、apps（登记表）、do_action

    # ---------- 等确认 ----------
    def _ask(self, what, run, emit, extra=None):
        now = time.time()
        for k in [k for k, v in self.ops.items() if now - v["at"] > OP_TTL]:
            self.ops.pop(k, None)
        oid = uuid.uuid4().hex[:8]
        self.ops[oid] = {"id": oid, "at": now, "what": what, "run": run}
        emit({"t": "ui", "action": "ask", "args": {"id": oid}, "title": what})
        r = {"needs_confirm": True, "id": oid, "what": what,
             "note": "还没做。用一句话告诉他要做什么，问他行不行；他明确同意后调用 confirm_action(id)。"}
        r.update(extra or {})
        return r

    def pending(self):
        now = time.time()
        return [{"id": v["id"], "what": v["what"]} for v in self.ops.values() if now - v["at"] < OP_TTL]

    def confirm(self, oid, said, yes, no, emit):
        said = (said or "").strip()
        if len(said) > 20 or not yes.search(said) or no.search(said):
            return {"error": "他最新一句话不是明确同意，不能做。先问他。"}
        op = self.ops.pop(str(oid or ""), None)
        if not op or time.time() - op["at"] > OP_TTL:
            return {"error": "这个操作已经过期或不存在，重新来一次"}
        try:
            r = op["run"]()
        except Exception as e:  # noqa: BLE001
            r = {"error": "出错了：%s" % type(e).__name__}
        emit({"t": "ui", "action": "done", "args": {"ok": not (isinstance(r, dict) and r.get("error"))},
              "title": op["what"] if not (isinstance(r, dict) and r.get("error")) else "没做成：" + str(r.get("error"))[:60]})
        return r

    # ---------- 分发 ----------
    def run(self, name, args, emit):
        fn = getattr(self, "t_" + name, None)
        if not fn:
            return {"error": "未知工具"}
        try:
            r = fn(args or {}, emit)
            if len(json.dumps(r, ensure_ascii=False, default=str)) > 9000:
                r = _clip(r)
            return r
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return {"error": "出错了：%s" % type(e).__name__}

    # ---------- 资讯 ----------
    def _news(self):
        n = self.ext.get("news")
        if not n:
            raise RuntimeError("资讯页没装上")
        return n

    def t_news_read(self, a, emit):
        snap = self._news().snapshot()
        ed = snap.get("edition")
        title = snap.get("title") or "Vesper 日报"
        if not ed:
            return {"error": "%s还没出第一期%s" % (title, "（正在编）" if snap.get("busy") else ""), "next": snap.get("next")}
        sec = a.get("section") or "overview"
        meta = {"title": title, "edition": ed.get("name"), "date": ed.get("date"), "made_at": _hm(ed.get("made_at")),
                "issue": ed.get("issue"), "next": snap.get("next"), "busy": snap.get("busy")}
        if sec == "overview":
            fr = ed.get("front") or {}
            return dict(meta, lead=_story(fr.get("lead")), briefs=[b.get("title") for b in fr.get("briefs") or []],
                        frontier_lead=(_story((ed.get("frontier") or {}).get("lead"), False) or {}).get("title"),
                        script_lines=len(ed.get("script") or []), sections=SECTIONS,
                        note="要细节就再按栏目读；要他听完整日报就用 news_brief(play)。")
        if sec == "script":
            return dict(meta, script=ed.get("script") or [])
        if sec == "front":
            fr = ed.get("front") or {}
            return dict(meta, lead=_story(fr.get("lead")), stories=[_story(s) for s in fr.get("stories") or []],
                        briefs=[_story(s, False) for s in fr.get("briefs") or []], picks=[_story(s) for s in ed.get("picks") or []])
        if sec == "frontier":
            fr = ed.get("frontier") or {}
            return dict(meta, lead=_story(fr.get("lead")),
                        beats=[{"beat": b.get("beat"), "items": [_story(s) for s in b.get("items") or []]} for b in fr.get("beats") or [] if b.get("items")],
                        papers=[_story(s, False) for s in fr.get("papers") or []])
        if sec == "giants":
            return dict(meta, week=ed.get("giants_week"),
                        companies=[{"company": g.get("company"), "items": [_story(s) for s in g.get("items") or []]} for g in ed.get("giants") or [] if g.get("items")])
        if sec == "oss":
            o = ed.get("oss") or {}
            row = lambda r: {k: r.get(k) for k in ("repo", "desc", "why", "lang", "stars_today", "stars", "downloads", "likes") if r.get(k)}  # noqa: E731
            return dict(meta, software=[row(r) for r in o.get("sw") or []], hardware=[row(r) for r in o.get("hw") or []],
                        models=[row(r) for r in o.get("md") or []])
        if sec == "art":
            ar = ed.get("art") or {}
            return dict(meta, lead=_story(ar.get("lead")), items=[_story(s) for s in ar.get("items") or []])
        if sec == "invest":
            return dict(meta, news=[dict(_story(s), symbols=s.get("symbols"), impact=s.get("impact")) for s in ed.get("invest") or []],
                        note="这是自选股相关的新闻，不是持仓；现价用 market_quotes。")
        return {"error": "没有这个栏目"}

    def t_news_brief(self, a, emit):
        act = a.get("action") or "play"
        if act not in ("play", "resume", "pause", "restart", "next", "prev"):
            return {"error": "不支持的动作"}
        snap = self._news().snapshot()
        ed = snap.get("edition")
        if act in ("play", "resume", "restart") and not (ed and ed.get("script")):
            return {"error": "这一期没有口播稿，可以用 news_read 读给他"}
        emit({"t": "ui", "action": "news_brief", "args": {"action": act}, "title": snap.get("title") or "Vesper 日报"})
        return {"ok": True, "edition": (ed or {}).get("name"), "lines": len((ed or {}).get("script") or []),
                "note": "已经交给资讯页的播放器，等你这句话说完就开始播。回答只说一句很短的话，别把日报内容再念一遍。" if act in ("play", "resume", "restart") else "已经在屏幕上做了"}

    def t_news_refresh(self, a, emit):
        import threading
        n = self._news()
        snap = n.snapshot()
        if snap.get("busy"):
            return {"ok": True, "note": "正在编这一期，稍等一两分钟"}
        if snap.get("spent_today", 0) >= snap.get("budget", 1e9):
            return {"error": "今天的资讯预算用完了，停在上一期"}
        threading.Thread(target=n.generate, kwargs={"manual": True}, daemon=True).start()
        return {"ok": True, "note": "开始重编了，大约 1～3 分钟后资讯页会换成新的一期"}

    def t_market_quotes(self, a, emit):
        n = self._news()
        m = n.market_status()
        for _ in range(6):  # 小助手按需启动，第一次要等几秒
            if m.get("state") not in ("starting",) and not m.get("stale"):
                break
            time.sleep(0.5)
            m = n.market_status()
        st = m.get("state")
        if st != "ok":
            why = {"not_installed": "长桥行情组件还没装", "setup": "长桥行情组件正在安装", "auth": "长桥还没授权（资讯页投资栏点「连接长桥」）",
                   "starting": "行情正在连接，过几秒再问"}.get(st, m.get("error") or "行情暂时拿不到")
            return {"error": why, "state": st}
        qs = m.get("quotes") or {}
        rows = []
        for sym, q in qs.items():
            r = {"symbol": sym, "name": q.get("name"), "last": q.get("last"), "change_pct": _pct(q.get("last"), q.get("prev_close"))}
            post = q.get("post") or q.get("pre")
            if isinstance(post, dict) and post.get("last"):
                r["ext_hours"] = {"kind": "盘后" if q.get("post") else "盘前", "last": round(post["last"], 2),
                                  "change_pct": _pct(post.get("last"), q.get("last"))}
            rows.append(r)
        want = str(a.get("symbol") or "").strip().upper()
        if want:
            hit = [r for r in rows if want in r["symbol"].upper() or want in str(r.get("name") or "").upper()]
            if not hit:
                return {"error": "自选里没有「%s」" % a.get("symbol"), "watchlist": [r["name"] or r["symbol"] for r in rows]}
            rows = hit
        out = {"quotes": rows, "updated": _hm(m.get("updated")), "stale": bool(m.get("stale"))}
        if not want:
            out["indices"] = [{"name": i.get("name"), "last": i.get("last"), "change_pct": _pct(i.get("last"), i.get("prev_close"))}
                              for i in m.get("indices") or []]
            out["temperature"] = m.get("temp")
            out["groups"] = [{"name": g.get("name"), "count": len(g.get("symbols") or [])} for g in m.get("groups") or []]
        out["note"] = "这是自选股行情（只读），不是持仓。"
        return out

    # ---------- 进程 ----------
    def t_top_processes(self, a, emit):
        import procs
        kind = a.get("kind") if a.get("kind") in ("cpu", "mem", "gpu") else "cpu"
        n = max(3, min(15, int(a.get("n") or 8)))
        r = procs.top_by(kind, n)
        rows = [{"pid": x["pid"], "name": x["name"], "cpu": x.get("cpu"), "mem_mb": round(x["mem"] / 1048576) if x.get("mem") else None,
                 "gpu": x.get("gpu"), "deck": x.get("own"), "can_quit": x.get("killable") and not x.get("own")} for x in r["rows"]]
        out = {"kind": kind, "rows": rows}
        if kind == "gpu" and not r.get("gpu_ok"):
            out["note"] = "这台机器拿不到每个进程的 GPU 占用"
        return out

    def t_process_quit(self, a, emit):
        import procs
        try:
            pid = int(a.get("pid"))
        except (TypeError, ValueError):
            return {"error": "pid 不对"}
        name, force = str(a.get("name") or ""), bool(a.get("force"))
        row = next((x for x in procs.snapshot()["rows"] if x["pid"] == pid), None)
        if not row:
            return {"error": "这个进程已经不在了"}
        if row["name"] != name:
            return {"error": "进程号 %d 现在是「%s」，不是「%s」" % (pid, row["name"], name)}
        if row.get("own"):
            return {"error": "「%s」是 Deck 自己要用的（%s），不能结束" % (name, row["own"])}
        if not row.get("killable"):
            return {"error": "「%s」是系统进程或不是你的进程，不能结束" % name}
        return self._ask(("强制结束" if force else "结束") + "进程「%s」" % name, lambda: procs.kill(pid, name, force), emit)

    # ---------- 磁盘 ----------
    def t_disk_usage(self, a, emit):
        import diskwatch
        vid = str(a.get("vol") or "mac")[:40]
        view = a.get("view") or "overview"
        gb = lambda b: round(b / 1e9, 1) if isinstance(b, (int, float)) else None  # noqa: E731
        if view == "overview":
            o = diskwatch.overview(vid)
            return {"volumes": [{"id": v["id"], "name": v["name"], "free_gb": gb(v["free"]), "total_gb": gb(v["total"]), "used_pct": v["pct"], "external": v["ext"]} for v in o["vols"]],
                    "today_drop_gb": gb(o.get("day_drop")), "biggest_grower": o.get("top") and {"app": o["top"]["app"], "gb": gb(o["top"]["delta"])},
                    "full_disk_access": o.get("fda")}
        if view == "grow":
            win = {"hour": 3600, "day": 86400, "week": 7 * 86400}.get(a.get("window"), 86400)
            g = diskwatch.growth(vid, win, 10)
            return {"window": a.get("window") or "day", "state": g.get("state"),
                    "rows": [{"name": r.get("app") or r.get("name"), "path": r.get("path"), "delta_gb": gb(r.get("delta")), "size_gb": gb(r.get("size"))} for r in g.get("rows") or []]}
        if view == "recent":
            r = diskwatch.recent(vid)
            for _ in range(10):
                if not r.get("loading"):
                    break
                time.sleep(1)
                r = diskwatch.recent(vid)
            return {k: v for k, v in r.items() if k in ("rows", "items", "loading", "error", "note")} or r
        if view == "writers":
            w = diskwatch.WRITERS.top(600)
            return w
        return {"error": "没有这个视图"}

    # ---------- GitHub ----------
    def t_github_repo(self, a, emit):
        src = self.f.hub.sources.get("github")
        if not src:
            return {"error": "GitHub 卡片没开"}
        q = str(a.get("repo") or "").strip().lower()
        repos = [r["nameWithOwner"] for r in getattr(src, "repos", []) or []]
        hit = [r for r in repos if r.lower() == q or r.split("/")[-1].lower() == q] or [r for r in repos if q and q in r.lower()]
        if not hit:
            return {"error": "GitHub 卡片里没有叫「%s」的仓库" % a.get("repo"), "repos": [r.split("/")[-1] for r in repos][:30]}
        d = src.detail(hit[0])
        if isinstance(d, dict) and not d.get("error"):
            d = {k: v for k, v in d.items() if k != "views"} if len(str(d)) > 6000 else d
        return {"repo": hit[0], "detail": d}

    # ---------- 应用 ----------
    def _find_app(self, q, registry_only=False):
        import apptools
        q = str(q or "").strip().lower().replace(" ", "")
        if not q:
            return None, None
        for a in (self.ext.get("apps") or (lambda: []))():
            names = [a.get("id", ""), a.get("name", ""), a.get("proc", "")]
            if any(q == n.lower().replace(" ", "") for n in names if n) or any(q in n.lower().replace(" ", "") for n in names if n and len(q) >= 3):
                return "reg", a
        if registry_only:
            return None, None
        apps = apptools.list_apps()
        exact = [x for x in apps if x["name"].lower().replace(" ", "") == q]
        part = [x for x in apps if q in x["name"].lower().replace(" ", "")]
        hit = exact or (part if len(part) == 1 else [])
        if hit:
            return "app", hit[0]
        return None, [x["name"] for x in part[:8]]

    def t_app_control(self, a, emit):
        import apptools
        act = a.get("action")
        kind, app = self._find_app(a.get("app"), registry_only=True)
        if kind != "reg":
            return self._app_generic(a.get("app"), act, emit)
        do = self.ext.get("do_action")
        name = app.get("name")
        if kind == "reg":
            if app.get("kind") == "agent":
                if act != "restart":
                    return {"error": "「%s」是后台服务，只能重启" % name}
                return self._ask("重启后台服务「%s」" % name, lambda: do(app["id"], "restart"), emit)
            if act in ("open", "reveal"):
                r = do(app["id"], act)
                if r.get("ok"):
                    emit({"t": "ui", "action": "app_" + act, "args": {}, "title": name})
                return r
            if act == "quit":
                return self._ask("退出「%s」" % name, lambda: do(app["id"], "quit"), emit)
            if act == "restart":
                def rs():
                    r = do(app["id"], "quit")
                    time.sleep(2.5)
                    return do(app["id"], "open") if r.get("ok") else r
                return self._ask("重启「%s」" % name, rs, emit)
            return {"error": "未知动作"}
        return {"error": "未知动作"}

    def _app_generic(self, q, act, emit):
        import apptools
        app, maybe = find_launchable(q)
        if not app:
            return {"error": "没找到叫「%s」的应用" % q, "maybe": maybe}
        name, path, bid = app["name"], app["path"], app["bid"]
        if not re.fullmatch(r"[A-Za-z0-9.\-_]{1,200}", bid):
            return {"error": "「%s」的 bundle id 不正常，不碰它" % name}
        if act == "open":
            ok = apptools._run(["open", "-b", bid], 15)[0] == 0
            if ok:
                emit({"t": "ui", "action": "app_open", "args": {}, "title": name})
            return {"ok": ok, "app": name} if ok else {"error": "没打开「%s」" % name}
        if act == "reveal":
            ok = apptools._run(["open", "-R", path], 15)[0] == 0
            if ok:
                emit({"t": "ui", "action": "app_reveal", "args": {}, "title": name})
            return {"ok": ok}
        if app["base"] in NO_QUIT or bid.startswith("io.github.deckformac.") or bid == "com.apple.finder":
            return {"error": "「%s」是系统或 Deck 自己要用的，不能从这里退出" % name}
        running = "%s/Contents/MacOS/" % path in apptools._running_apps()

        def quit_():
            code, _, err = apptools._run(["osascript", "-e", 'tell application id "%s" to quit' % bid], 20)
            return {"ok": code == 0, "error": None if code == 0 else "没能退出：%s" % err.strip()[:120]}
        if act == "quit":
            if not running:
                return {"ok": True, "note": "「%s」本来就没开" % name}
            return self._ask("退出「%s」" % name, quit_, emit)
        if act == "restart":
            def rs():
                r = quit_() if running else {"ok": True}
                time.sleep(2.5)
                return {"ok": apptools._run(["open", "-b", bid], 15)[0] == 0} if r.get("ok") else r
            return self._ask("重启「%s」" % name, rs, emit)
        return {"error": "未知动作"}

    def t_uninstall_app(self, a, emit):
        import apptools
        kind, app = self._find_app(a.get("app"))
        if kind == "reg":
            path = app.get("path")
            hit = next((x for x in apptools.list_apps() if x["path"] == path), None) if path else None
            if not hit:
                return {"error": "「%s」不是普通应用，不能这样卸载" % app.get("name")}
            app = hit
        elif not kind:
            return {"error": "没找到叫「%s」的应用" % a.get("app"), "maybe": app or []}
        plan = apptools.uninstall_plan(app["path"])
        if plan.get("error"):
            return plan
        picks = [i["i"] for i in plan["items"] if i["on"] and not i["admin"]]
        size = sum(i["bytes"] or 0 for i in plan["items"] if i["i"] in picks)
        skipped = [i["path"] for i in plan["items"] if not i["on"] or i["admin"]]
        what = "卸载「%s」（%d 项，共 %s，移到废纸篓）" % (app["name"], len(picks), apptools.human(size))
        return self._ask(what, lambda: apptools.execute(plan["id"], picks), emit,
                         {"items": [i["path"] for i in plan["items"] if i["i"] in picks][:12], "not_touched": skipped[:8],
                          "running": app.get("running")})

    # ---------- 工具箱 ----------
    def t_toolbox_scan(self, a, emit):
        import apptools
        kind = a.get("kind")
        if kind == "apps":
            apps = apptools.list_apps()
            return {"count": len(apps), "apps": [{"name": x["name"], "version": x["version"], "mb": round((x["bytes"] or 0) / 1048576),
                                                  "running": x["running"]} for x in apps if not x["protected"]][:80]}
        fn = {"space": apptools.space_overview, "junk": apptools.junk_scan, "files": apptools.files_scan, "updates": apptools.updates_check}.get(kind)
        if not fn:
            return {"error": "没有这种扫描"}
        args = (200,) if kind == "files" else ()
        jid = apptools.start_job(kind, fn, *args)
        t0 = time.time()
        j = apptools.job(jid)
        while j.get("state") == "running" and time.time() - t0 < 40:
            time.sleep(1)
            j = apptools.job(jid)
        if j.get("state") == "running":
            return {"running": True, "note": "还在扫（%s），过一会儿再问我或者打开工具箱看" % (j.get("progress") or "")}
        r = j.get("result") or {}
        if isinstance(r, dict) and r.get("error"):
            return r
        h = apptools.human
        if kind == "junk":
            return {"plan_id": r.get("id"), "total": h(r.get("bytes") or 0),
                    "rules": [{"id": x["id"], "label": x.get("label"), "size": h(x.get("bytes") or 0), "count": x.get("count"), "default_on": x.get("on")}
                              for x in r.get("rules") or []],
                    "note": "要清的话调用 junk_clean(plan_id)（先确认）。清单一小时内有效。"}
        if kind == "space":
            rows = r if isinstance(r, list) else []
            return {"rows": [{"path": x.get("path"), "size": h(x["bytes"]) if x.get("bytes") is not None else "太大没算完"} for x in rows[:15]]}
        if kind == "files":
            items = r.get("items") or []
            return {"count": len(items), "items": [{k: x.get(k) for k in ("path", "bytes", "kind", "age_days", "dup") if k in x} for x in items[:15]],
                    "note": "整理文件请在工具箱里勾选后点按钮（Vesper 不替你挑文件删）。"}
        if kind == "updates":
            pick = lambda xs: [{k: x.get(k) for k in ("name", "token", "from", "to") if x.get(k)} for x in xs][:20]  # noqa: E731
            return {"brew_apps": pick(r.get("brew") or []), "brew_tools": pick(r.get("formula") or []), "sparkle": pick(r.get("sparkle") or []),
                    "app_store": pick(r.get("mas") or []),
                    "note": "升级请在工具箱的「更新」里点。"}
        return r

    def t_junk_clean(self, a, emit):
        import apptools
        pid = str(a.get("plan_id") or "")
        plan = apptools.PLANS.get(pid)
        if not plan or plan.get("kind") != "junk":
            return {"error": "清单过期了，先 toolbox_scan(junk) 重新扫"}
        rules = plan["cplan"]["rules"]
        want = [str(x) for x in (a.get("rules") or [])] or [r["id"] for r in rules if r["on"]]
        pick = [r for r in rules if r["id"] in want]
        if not pick:
            return {"error": "没有勾选任何项目"}
        size = sum(r["bytes"] for r in pick)
        return self._ask("把 %s 垃圾移到废纸篓（%s）" % (apptools.human(size), "、".join(r.get("label", r["id"]) for r in pick[:4])),
                         lambda: apptools.junk_execute(pid, [r["id"] for r in pick]), emit)

    # ---------- 设置 / 显示方式 ----------
    def t_deck_setting(self, a, emit):
        name, val = a.get("name"), str(a.get("value") or "").strip().lower()
        if name == "card":
            key = str(a.get("card") or "").strip()
            snap = self.f.hub.snapshot()
            if key not in snap["sources"]:
                hit = [k for k, s in snap["sources"].items() if key and (key in s["title"] or s["title"] in key)]
                if not hit:
                    return {"error": "没有这张卡片", "cards": {k: s["title"] for k, s in snap["sources"].items()}}
                key = hit[0]
            show = val in ("show", "on", "true", "1", "显示")
            title = snap["sources"][key]["title"]
            emit({"t": "ui", "action": "deck_setting", "args": {"name": "card", "card": key, "value": "show" if show else "hide"}, "title": title})
            return {"ok": True, "note": ("显示了「%s」" if show else "隐藏了「%s」（设置里能再打开）") % title}
        opts = SETTINGS.get(name)
        if not opts:
            return {"error": "没有这个设置"}
        if name in ("sfx", "reduce"):
            val = "on" if val in ("on", "true", "1", "开", "打开") else "off" if val in ("off", "false", "0", "关", "关闭") else val
        if val not in opts:
            return {"error": "可选值：%s" % "、".join("%s=%s" % kv for kv in opts.items())}
        emit({"t": "ui", "action": "deck_setting", "args": {"name": name, "value": val}, "title": "%s：%s" % (SETTING_NAMES[name], opts[val])})
        return {"ok": True, "note": "已经改成%s「%s」" % (SETTING_NAMES[name], opts[val])}

    def t_display_mode(self, a, emit):
        m = a.get("mode")
        if m not in ("auto", "ext", "notch"):
            return {"error": "只能是 auto / ext / notch"}
        label = {"auto": "自动", "ext": "扩展屏", "notch": "刘海"}[m]
        emit({"t": "ui", "action": "display_mode", "args": {"mode": m}, "title": label})
        return {"ok": True, "note": "显示方式改成「%s」了（只在 Deck 应用里有效，网页里打开时没这个开关）" % label}

    # ---------- 每日清理 ----------
    def t_daily_cleanup(self, a, emit):
        import daily_clean
        act = a.get("action")
        if act in ("auto_on", "auto_off"):
            daily_clean.set_auto(act == "auto_on")
            hub = self.f.hub
            try:
                hub.refresh("cleanup")
            except Exception:  # noqa: BLE001
                pass
            return {"ok": True, "note": "每晚 23:30 核对完直接移进废纸篓" if act == "auto_on" else "每晚 23:30 先问他再清"}
        if act == "undo":
            rid = str(a.get("run_id") or "")
            if not rid:
                runs = list(self.f._runs())
                if not runs:
                    return {"error": "没有可以放回的清理记录"}
                rid = sorted(runs, key=lambda k: self.f._runs()[k]["at"])[-1]
            r = self.f.cleanup_undo(rid)
            if not r.get("error"):
                emit({"t": "ui", "action": "done", "args": {"ok": True}, "title": "放回了 %s 项" % r.get("restored")})
                self.f.hub.refresh("cleanup")
            return r
        st = daily_clean.state()
        st = {k: (_hm(v) if k in ("next_at", "checked_at") and isinstance(v, (int, float)) else v)
              for k, v in st.items() if k in ("auto", "last_daily", "next_at", "checked_at", "pending")}
        runs = self.f._runs()
        st["undo_runs"] = [{"run_id": k, "at": _hm(v["at"]), "count": len(v.get("moved") or [])} for k, v in sorted(runs.items(), key=lambda kv: -kv[1]["at"])]
        return st
