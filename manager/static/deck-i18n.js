/* Deck 英文模式 / English mode
 *
 * 选语言：设置 → 外观 → 语言（跟随系统 / 中文 / English），存在 localStorage "deck.lang"；
 * 网址加 ?lang=en 或 ?lang=zh 可以临时指定（截图、调试用）。
 * 跟随系统：浏览器首选语言是中文就用中文，否则英文。
 *
 * 做法：页面照旧用中文渲染，这个脚本盯着 DOM，把「整段完全对上」的中文界面文字换成英文
 * （词典 D 精确匹配 + 规则 R 匹配带数字的句子）。对不上的（比如和助手的聊天原文、日报正文）原样保留，
 * 不会拼出半中半英的句子。新加的界面文字，在词典里补一行即可。
 */
(function () {
  "use strict";
  var KEY = "deck.lang";
  function pick() {
    try { var q = new URLSearchParams(location.search).get("lang"); if (q === "en" || q === "zh") return q; } catch (e) {}
    try { var pl = window.parent !== window && window.parent.DeckLang && window.parent.DeckLang.lang; if (pl === "en" || pl === "zh") return pl; } catch (e) {}  // 嵌着的管家页跟着 Deck
    var s = ""; try { s = localStorage.getItem(KEY) || ""; } catch (e) {}
    if (s === "en" || s === "zh") return s;
    var nl = (navigator.languages && navigator.languages[0]) || navigator.language || "zh";
    return /^zh/i.test(nl) ? "zh" : "en";
  }
  var LANG = pick();
  function pref() { try { return localStorage.getItem(KEY) || "auto"; } catch (e) { return "auto"; } }
  var api = {
    lang: LANG, pref: pref,
    set: function (v) {
      try { if (v === "en" || v === "zh") localStorage.setItem(KEY, v); else localStorage.removeItem(KEY); } catch (e) {}
      try { (window.top || window).location.reload(); } catch (e) { location.reload(); }
    },
    t: function (s) { return s; }
  };
  window.DeckLang = api;

  // ---------- Windows 版（11-08）：Mac 的说法换成 Windows 的 ----------
  // 服务端在 Windows 上给 <html> 加 os-win。中文模式在这里换；英文模式在翻译之后换（见 tr 后面的 osFix）。
  var WIN = /(^|\s)os-win(\s|$)/.test(document.documentElement ? document.documentElement.className : "");
  var ZW = [[/清空废纸篓/g, "清空回收站"], [/废纸篓/g, "回收站"], [/在访达中显示/g, "在资源管理器中显示"], [/在访达里/g, "在资源管理器里"],
    [/访达/g, "资源管理器"], [/程序坞/g, "任务栏"], [/Dock 失效图标/g, "任务栏失效图标"], [/在 Dock/g, "在任务栏"],
    [/自建后台服务（LaunchAgent）/g, "自建后台任务（任务计划程序）"], [/LaunchAgent/g, "计划任务"], [/钥匙串/g, "凭据管理器"],
    [/这台 Mac/g, "这台电脑"], [/Mac 管家/g, "电脑管家"], [/Spotlight/g, "Windows 搜索"], [/活动监视器/g, "任务管理器"], [/完全磁盘访问权限/g, "文件夹访问权限"],
    [/⌃?⌥⌘M/g, "Ctrl+Alt+M"], [/⌃?⌥⌘J/g, "Ctrl+Alt+J"], [/⌃?⌥⌘\./g, "Ctrl+Alt+."],
    [/打开 DeckVoice（菜单栏的麦克风图标）后再来/g, "重新打开 Deck（语音服务会跟着起来）"], [/打开 DeckVoice 后/g, "Deck 起来后"], [/DeckVoice 会自动重开/g, "Deck 会自动重开"]];
  var EW = [[/Empty the Trash/g, "Empty the Recycle Bin"], [/\bthe Trash\b/g, "the Recycle Bin"], [/\bTrash\b/g, "Recycle Bin"],
    [/in Finder/g, "in File Explorer"], [/\bFinder\b/g, "File Explorer"], [/\bthe Dock\b/g, "the taskbar"], [/\bDock\b/g, "Taskbar"],
    [/LaunchAgents?/g, "scheduled task"], [/\bKeychain\b/g, "Credential Manager"], [/this Mac\b/g, "this PC"], [/Mac Butler/g, "PC Butler"], [/Activity Monitor/g, "Task Manager"],
    [/⌃?⌥⌘M/g, "Ctrl+Alt+M"], [/⌃?⌥⌘J/g, "Ctrl+Alt+J"], [/⌃?⌥⌘\./g, "Ctrl+Alt+."], [/⌃?⌥⌘/g, "Ctrl+Alt+"], [/Open DeckVoice/g, "Restart Deck"], [/DeckVoice/g, "Deck"]];
  function swap(s, list) { for (var i = 0; i < list.length; i++) s = s.replace(list[i][0], list[i][1]); return s; }
  var ZH = /[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/;
  // 中文句子（含没翻译的）按中文换；纯英文（翻译结果、原本就是英文的 Dock 之类）按英文换
  function osFix(s) { return !WIN || !s ? s : (LANG !== "en" || ZH.test(s)) ? swap(s, ZW).replace(/\bDock\b/g, "任务栏") : swap(s, EW); }
  api.osFix = osFix;
  if (WIN && LANG !== "en") {
    var fixNode = function (n) { var p = n.parentNode; if (p && !/^(SCRIPT|STYLE|TEXTAREA)$/.test(p.nodeName)) { var o = osFix(n.nodeValue); if (o !== n.nodeValue) n.nodeValue = o; } };
    var fixAttr = function (el) { ["placeholder", "title", "aria-label", "aria-description"].forEach(function (a) { var v = el.getAttribute && el.getAttribute(a); if (v) { var o = osFix(v); if (o !== v) el.setAttribute(a, o); } }); };
    var fixTree = function (root) {
      if (!root) return;
      if (root.nodeType === 3) return fixNode(root);
      if (root.nodeType !== 1 && root.nodeType !== 11) return;
      if (root.nodeType === 1) fixAttr(root);
      var w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT), n;
      while ((n = w.nextNode())) { if (n.nodeType === 3) fixNode(n); else fixAttr(n); }
    };
    var zstart = function () {
      fixTree(document.body); document.title = osFix(document.title);
      new MutationObserver(function (ms) {
        for (var i = 0; i < ms.length; i++) {
          var m = ms[i];
          if (m.type === "characterData") fixNode(m.target);
          else if (m.type === "attributes") fixAttr(m.target);
          else for (var j = 0; j < m.addedNodes.length; j++) fixTree(m.addedNodes[j]);
        }
      }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["placeholder", "title", "aria-label", "aria-description"] });
    };
    if (document.body) zstart(); else document.addEventListener("DOMContentLoaded", zstart);
  }
  if (LANG !== "en") return;
  if (document.documentElement) document.documentElement.lang = "en";

  var CJK = /[　-〿一-鿿＀-￯]/;

  // ---------- 词典：整段精确对上才换 ----------
  var D = {
    // 顶栏 / 页签
    "一切正常": "All good", "马上": "shortly",
    "语音服务没在运行（DeckVoice 会自动重开，几分钟后还这样就告诉 Claude）": "the voice service isn't running (DeckVoice restarts it automatically; if it stays like this for a few minutes, tell Claude)",
    "正在读取…（约 1 秒）": "Reading… (about 1 s)", "正在看哪些没必要、哪些不正常…": "Checking what's unnecessary or unusual…",
    "每 3 秒刷新，数据和「活动监视器」同源。「退出」相当于正常退出（未保存的内容可能丢失），「强制」会立刻结束；系统进程和别的用户的进程不能结束。Vesper 只在你打开这张卡时看一次，1 分钟内不重复花钱。":
      "Refreshes every 3 s, same source as Activity Monitor. “Quit” is a normal quit (unsaved work may be lost); “Force” ends it at once. System processes and other users' processes can't be ended. Vesper looks once when you open this card and won't spend again within 1 minute.",
    "Deck 监控台的后台服务，结束了监控台就停了": "Deck's dashboard backend; ending it stops the dashboard",
    "「1 小时」": "“1 h”", "最早能看到。": " to see it soonest.",
    "监控台": "Dashboard", "管家": "Butler", "资讯": "News", "添加": "Add", "完成": "Done", "撤销": "Undo", "重试": "Retry",
    "连不上 Mac 管家": "Can't reach Mac Butler", "确认 Mac 没睡眠、Mac 管家在运行，iPad 和 Mac 在同一个 Wi-Fi。": "Make sure the Mac is awake, Mac Butler is running, and the iPad is on the same Wi-Fi.",
    "开始对话": "Start chat", "按住 ← 说话": "Hold \u2190 to talk", "正在连接…": "Connecting…", "卡片怎么排": "Card layout", "在听…": "Listening…", "在想…": "Thinking…", "在说…": "Speaking…", "回到卡片": "Back to cards", "点下面和我聊": "Tap below to talk",
    // 状态
    "正常": "OK", "注意": "Check", "异常": "Error", "过期": "Stale", "加载中": "Loading", "刚刚": "just now",
    // Mac 实时状态
    "Mac 实时状态": "Mac Live Status", "内存": "Memory", "磁盘": "Disk", "电池": "Battery", "充电中": "charging", "已充满": "charged",
    "系统数据正常": "System data OK", "芯片": "Chip", "交换": "Swap", "显存占用": "VRAM in use", "磁盘剩余": "Disk free", "已开机": "Uptime",
    "系统数据（每 5 分钟看一次）": "System data (checked every 5 min)", "磁盘可用": "Disk free", "Chrome 残留": "Chrome leftovers", "自我副本": "self-copies",
    "Codex 插件": "Codex plugin", "市场暂存": "marketplace cache", "内存 最近 3 分钟": "Memory · last 3 min", "占用 CPU 最多": "Top CPU", "立即刷新": "Refresh now",
    // Claude / Codex
    "5 小时": "5 h", "剩余": "left", "本周": "This week", "运行中的任务": "Running tasks", "没有运行中的任务": "No running tasks", "套餐": "Plan",
    "今日": "Today", "最近 5 小时": "Last 5 hours", "最近 24 小时（每小时）": "Last 24 hours (hourly)", "输入": "Input", "输出": "Output", "缓存写": "Cache write", "缓存读": "Cache read", "调用": "Calls",
    "今日各模型": "Today by model", "最近的会话": "Recent sessions",
    "数据来自本机 ~/.claude/projects 的会话日志，只取用量字段，不读对话内容。": "From local session logs in ~/.claude/projects. Only usage fields are read, never the conversation.",
    "每分钟临时启动一次本机 codex app-server 询问，问完就退出。": "Asks a local codex app-server once a minute, then exits.",
    // DeepSeek
    "查余额": "Check", "合计约": "Total ≈", "赠送": "Granted", "充值": "Topped up", "能用": "works", "未运行": "not running",
    "Harness 用量": "Harness usage", "桌面版账本": "Desktop ledger", "次调用": "calls", "最近会话": "Recent sessions",
    // 每日清理
    "每日清理": "Daily Cleanup", "现在没什么可清的": "nothing to clean right now", "DeepSeek 逐项核对，只进废纸篓": "DeepSeek checks each item · Trash only",
    "每晚自动清（不先问我）": "Clean nightly without asking", "全部放回": "Put all back", "现在能清的，按类": "Cleanable now, by type",
    "系统数据暴涨时的自动清理": "Auto-cleanup when system data surges", "DeepSeek 拿不准、留着没清的": "Kept because DeepSeek wasn't sure",
    "AI 工具旧记录": "Old AI tool records", "系统残留": "System leftovers", "下载缓存": "Download caches",
    "，只进废纸篓": ", Trash only", "每晚 23:30 DeepSeek 核对后清理": "Cleans nightly at 23:30 after DeepSeek checks",
    "设置备份，可能含配置或凭据，保留": "settings backup, may hold config or credentials, kept",
    "Codex 配置备份，可能含密钥，保留": "Codex config backup, may hold keys, kept",
    "没有可清理的东西": "Nothing to clean",
    "每 3 小时检测一次能腾出多少（不动文件，不花钱）；每晚 23:30 把清单交给 DeepSeek 逐项核对，只清 AI 生成的测试文件、临时文件、缓存和旧安装包，拿不准的留着。东西都在废纸篓里，清空之前随时能放回。": "Every 3 hours it checks how much could be freed (touching nothing, costing nothing). Every night at 23:30 DeepSeek reviews the list item by item and only clears AI-generated test files, temp files, caches and old installers; anything uncertain is kept. Everything goes to the Trash and can be put back until you empty it.",
    // 我的应用
    "我的应用": "My Apps", "应该常驻，但现在没在运行": "should stay running but isn't", "开发状态": "Dev status",
    "测显存、DeepSeek 排查等操作在 Mac 管家页面里做。": "VRAM tests and DeepSeek checks live on the Butler page.",
    // GitHub
    "全部": "All", "有事": "Needs you", "公开": "Public", "私人": "Private", "睡着的": "Dormant", "仓库": "repos", "星": "stars",
    "浏览（30 天）": "views (30 d)", "件事要你看": "need you", "在 GitHub 打开": "Open on GitHub", "要你看：": "Needs you:", "看失败日志": "See failure log",
    "浏览 30 天": "views 30 d", "克隆 30 天": "clones 30 d", "Fork": "Fork",
    "浏览量 · 最近 30 天（Mac 每天存一份，不受 GitHub 只留 14 天的限制）": "Views · last 30 days (the Mac saves a copy daily, beyond GitHub's 14-day limit)",
    "浏览": "Views", "独立访客": "Unique visitors", "最近提交": "Recent commits", "访客从哪来（14 天）": "Referrers (14 d)", "还没有来源数据": "No referrer data yet",
    "发布": "Releases", "还没发过版本": "No releases yet", "最近 30 天还没人看过": "No views in the last 30 days", "CI 失败": "CI failed", "CI 通过": "CI passed", "CI 跑着": "CI running",
    "本机没有副本": "no local copy", "今天": "today", "（还没写说明）": "(no description)",
    "Mac 管家全家": "Mac Butler family", "个人项目": "Personal projects", "桌面小工具": "Desktop tools",
    // 工具箱
    "工具箱": "Toolbox", "卸载应用": "Uninstall", "垃圾清理": "Junk", "文件": "Files", "更新": "Update", "小工具": "Tools",
    "按名字": "By name", "按大小": "By size", "最久没用的在前": "Least used first", "卸载…": "Uninstall…", "开着": "running", "系统应用": "System app",
    "检查更新": "Check for updates", "Homebrew 装的应用": "Apps installed with Homebrew", "App Store": "App Store", "打开 App Store": "Open App Store",
    "所有东西只会移进废纸篓，不会直接删除。清空废纸篓之前，都能在这里一键撤销放回原处。": "Everything only goes to the Trash, nothing is deleted outright. Until you empty the Trash, one tap here puts it back.",
    "不动文件的随手小工具，以后新加的也放在这一页。": "Handy tools that never touch your files. New ones will land on this page too.",
    "清洁": "Cleaning", "擦键盘": "Wipe the keyboard", "锁住以后按什么都没反应，擦完再解锁": "Locks the keyboard so nothing reacts, unlock when you're done",
    "锁多久": "Lock for", "黑屏": "Black screen", "灰尘和指纹看得更清楚，也方便顺手擦屏幕": "Dust and fingerprints show up better, and you can wipe the screen too",
    "连触控板一起锁": "Lock the trackpad too", "开始清洁": "Start cleaning", "怎么解锁（任何一种都行）": "How to unlock (any one works)",
    "电源键和 Touch ID 直连硬件，软件挡不住，擦到那里轻一点；按到让 Mac 睡眠时会自动解锁。": "The power button and Touch ID are wired to hardware and can't be blocked, so go gently there; if the Mac sleeps, it unlocks automatically.",
    "锁着时只能用 ⌘⌘ 或倒计时解锁，最多 2 分钟": "While locked, only ⌘⌘ or the timer unlocks it, 2 min max",
    "屏幕上的「按住解锁」按住 1 秒": "Hold the on-screen “Hold to unlock” for 1 s", "同时按住左右两个 ⌘ 2 秒（中间别碰别的键）": "Hold both ⌘ keys for 2 s (don't touch other keys)",
    "倒计时结束自动解锁": "Unlocks by itself when the timer ends", "在这里点「解锁」，或对 Vesper 说「解锁键盘」": "Tap “Unlock” here, or tell Vesper “unlock the keyboard”",
    "合盖、睡眠、锁屏时自动解锁": "Unlocks when you close the lid, sleep or lock the screen", "正在看键盘的状态…": "Checking the keyboard…",
    "正在看助手的状态…": "Checking the assistant…", "正在看声纹的状态…": "Checking the voiceprint…", "正在列出应用…": "Listing apps…", "正在第一次量各个文件夹有多大，要几分钟。量完以后，过 1 小时就能看出谁在涨。": "Measuring every folder for the first time, which takes a few minutes. An hour after that you'll see what's growing.", "正在看…": "Checking…",
    "录入": "Record", "开始录入": "Start recording", "重新录入": "Record again", "录入已退出，这次录的没保存，原来的声纹还在": "Recording quit. Nothing from this session was saved; your old voiceprint is still there.",
    "换显示屏、铺满屏幕、开机自启：点菜单栏里的监控台图标。": "Switch displays, go full screen, launch at login: click the Deck icon in the menu bar.",
    "iPad 一直亮屏：设置 → 显示与亮度 → 自动锁定 → 永不，并接上电源。": "Keep the iPad awake: Settings → Display & Brightness → Auto-Lock → Never, and plug it in.",
    "数据都在你的 Mac 上采集；页面每 2 秒拿一次缓存好的结果，没人看时 Mac 会自动停采。": "All data is collected on your Mac; the page fetches cached results every 2 s, and the Mac stops collecting when nobody is looking.",
    "系统残留 ·": "System leftovers ·", "下载缓存 ·": "Download caches ·", "可能在用或没权限": "maybe in use or no permission",
    "选硬盘": "Choose drive", "新出现": "new", "应用本身": "Apps themselves", "应用数据": "App data", "桌面": "Desktop", "Codex 的工作区": "Codex workspace",
    "Claude Code 的会话和设置": "Claude Code sessions & settings", "这一天少了": "Shrank today",
    "包括缓存、资源库和隐藏文件夹（比如": "Includes caches, Library and hidden folders (e.g. ", "）。能清的缓存交给每晚": "). Cleanable caches are left to the nightly ",
    "的清理（只进废纸篓）。点一行在访达里显示。": " cleanup (Trash only). Tap a row to show it in Finder.",
    "）。微信这类应用的沙盒没算进来：要给 python3 完全磁盘访问权限才能量，没给是为了不弹隐私窗口。能清的缓存交给每晚": "). Sandboxes of apps like WeChat aren't counted: measuring them needs Full Disk Access for python3, left off to avoid privacy prompts. Cleanable caches are left to the nightly ",
    "这是应用往硬盘里写了多少，不等于多占了多少：反复改同一个文件、写日志再删掉都会算进去。一直很高、又对得上": "This is how much apps wrote to disk, not how much extra space they take: rewriting a file or writing and deleting logs all count. If one stays high and also shows up under ",
    "「谁在变大」": "“Growing”", "的，基本就是它。只看得到你自己用户的应用。": ", that's most likely it. Only your own user's apps are visible.",
    "今日 token": "Tokens today", "没有要你管的": "Nothing for you to handle", "没什么要你处理的": "Nothing needs you right now",
    "没找到 Codex 命令行。装 ChatGPT 桌面版或 `npm i -g @openai/codex` 后再试。": "Codex CLI not found. Install the ChatGPT desktop app or `npm i -g @openai/codex`, then try again.",
    "打开后，同一个 Wi-Fi 下的 iPad 可以直接用 Safari 看这个监控台，不再需要把 iPad 当 Mac 的副屏。第一次 macOS 可能会问“是否允许 python3 接受传入连接”，点允许。": "Once on, an iPad on the same Wi-Fi can open this dashboard in Safari, no need to use it as a second screen. The first time, macOS may ask whether python3 can accept incoming connections; click Allow.",
    "语音服务没在运行，打开 DeckVoice 后这里可以改名字、录声纹、做测试": "The voice service isn't running. Open DeckVoice to rename, record a voiceprint and run tests here.",
    "没有匹配的应用": "No matching apps", "还没有清理记录。": "No cleanups yet.", "这里没有仓库": "No repos here", "在左边点一个仓库看详情": "Pick a repo on the left to see details",
    "读取中…": "Loading…", "读不到": "Can't read", "有问题": "Issue", "无。": "None.", "还没找到 DeepSeek key。": "No DeepSeek key found yet.",
    "长桥 · 我的自选": "Longbridge · My watchlist", "连接长桥，看你的自选股实时行情": "Connect Longbridge to see live quotes for your watchlist",
    "点下面的按钮。第一次会先装长桥官方 SDK（一两分钟）。": "Tap the button below. The first time, it installs the official Longbridge SDK (a minute or two).",
    "Mac 上会打开长桥授权页，用真实账户登录（不是模拟账户）。": "The Longbridge authorization page opens on your Mac; sign in with your real account (not a paper account).",
    "权限只勾「自选」，不要勾交易。Deck 只读行情，不会下单。": "Grant only “Watchlist”, not trading. Deck only reads quotes and never places orders.",
    "连上长桥后，这里会放和你自选股有关的新闻，并标出利好还是利空。": "Once Longbridge is connected, news about your watchlist shows up here, marked bullish or bearish.",
    "今晚 23:30 要清的": "Clearing tonight at 23:30", "这把 key 的余额 ·": "Balance on this key ·", "、": ", ",
    "Vesper 自己的 DeepSeek 用量": "Vesper's own DeepSeek usage", "这把 key 的余额": "Balance on this key", "累计": "All time", "近 7 天": "Last 7 d", "近 30 天": "Last 30 d",
    "也可以直接问 Vesper「你用了多少」。": "You can also just ask Vesper “how much have you used”.",
    // 磁盘
    "磁盘 · 谁在占空间": "Disk · What's using space", "量满一天后显示": "shown after a full day", "还没看出谁在涨": "nothing growing yet",
    "谁在变大": "Growing", "最近新增": "New", "谁在写硬盘": "Writing", "最近 10 分钟": "Last 10 min", "开着以来": "Since start",
    "每 5 秒刷新": "Refreshes every 5 s", "正在读取…": "Reading…", "1 小时": "1 h", "7 天": "7 d", "系统": "system",
    // 进程
    "进程 · CPU": "Processes · CPU", "Vesper 的建议": "Vesper's advice", "重新看看": "Look again", "看起来都正常。": "Everything looks normal.", "进程": "Processes",
    // 设置
    "设置": "Settings", "卡片（也可以点顶栏的 ▦ 直接拖动、调大小、添加网页）": "Cards (or tap ▦ in the top bar to drag, resize and add web pages)",
    "卡片（拖动、调大小、添加网页在扩展屏模式里做）": "Cards (drag, resize and add web pages in external-screen mode)",
    "外观": "Appearance", "皮肤": "Skin", "新拟态": "Neumorphic", "玻璃": "Glass", "深浅色": "Light / dark", "跟随系统": "System", "深色": "Dark", "浅色": "Light",
    "一屏看完": "Fit one screen", "可滚动": "Scrollable", "刘海模式面板大小": "Notch panel size", "小": "S", "中": "M", "大": "L",
    "刘海里卡片怎么排": "Cards in the notch", "上下滑": "Scroll", "左右翻页": "Swipe pages", "显示方式": "Display", "自动": "Auto", "扩展屏": "External screen", "刘海": "Notch",
    "语言": "Language", "中文": "中文", "English": "English",
    "Vesper 玻璃透明度": "Vesper glass opacity", "Vesper 的声音": "Vesper's voice", "试听": "Preview", "语速": "Speed", "慢一点": "Slower", "快一点": "Faster",
    "Vesper 的记忆": "Vesper's memory", "查看": "View", "AI 思考光效": "AI thinking glow", "预览 6 秒": "Preview 6 s", "按键音效": "Button sounds", "轻": "Soft", "响": "Loud",
    "夜里 23:00–7:00 自动减半；Vesper 在听、在说时不响。": "Halved at night 23:00–7:00; silent while Vesper listens or speaks.", "减少动画": "Reduce motion",
    "助手": "Assistant", "改名字": "Rename", "声纹": "Voiceprint", "重录": "Re-record", "测试": "Test", "去测试": "Test",
    "叫十遍看能醒几次、请别人叫看会不会被冒充、安静听几分钟看会不会误醒": "Call it ten times to see how often it wakes, have someone else try to impersonate you, and listen quietly to catch false wakes",
    "iPad 访问": "iPad access", "开启 iPad 访问": "Turn on iPad access", "小贴士": "Tips",
    "Vesper · 女声（清亮带笑）": "Vesper · female (bright, smiling)", "Vesper · 男声（干净温暖）": "Vesper · male (clean, warm)",
    "温柔慵懒的女生": "Soft, lazy female", "元气少女": "Energetic girl", "知性助理女声": "Composed assistant, female", "低沉的 AI 管家男声": "Deep AI butler, male",
    "插着 iPad 用扩展屏，拔掉就从刘海弹出。": "External screen while the iPad is plugged in; from the notch once it's unplugged.",
    "插着 iPad 用扩展屏，拔掉就从刘海弹出。现在用的是": "External screen while the iPad is plugged in, the notch once it's unplugged. Now using the ",
    "一直铺在 iPad（或你选的那块屏）上，刘海不弹出。现在用的是": "Always on the iPad (or the screen you picked); the notch stays closed. Now using the ",
    "鼠标碰到刘海就从顶上弹出，⌥空格也能呼出。现在用的是": "Drops down when the pointer touches the notch, or press ⌥Space. Now using the ",
    "。菜单栏的 Deck 图标里也能切。": ". You can also switch from the Deck icon in the menu bar.",
    "正常说": "Normal voice", "再说一遍": "Once more", "小声说": "Quietly", "戴着耳机说": "With headphones", "换个叫法": "Another way", "英文": "English", "读一句话": "Read a sentence",
    "可跳过": "optional", "录这一遍": "Record this take", "重录这一遍": "Re-record", "在听，开口说吧…": "Listening, go ahead…", "正在检查这一遍…": "Checking this take…",
    "准备好了就点「录这一遍」": "Tap \u201cRecord this take\u201d when you're ready", "跳过": "Skip",
    // 助手 · 名字 / 声纹
    "名字": "Name", "读法（可选）": "Pronunciation (optional)", "体检这个名字": "Check this name", "用这个名字": "Use this name", "改名以后": "After renaming",
    "英文 3–12 个字母（一个词），或 2–4 个汉字。少见、两三个音节的名字最好叫醒；Siri、小爱这类别的助手的名字不能用。": "3–12 English letters (one word) or 2–4 Chinese characters. Rare names with two or three syllables wake best; other assistants' names like Siri can't be used.",
    "唤醒马上换成新名字，不用重启。原来的声纹先留着用，但它是按旧名字录的，叫醒率会低一些，最好到「声纹」重录一次（大约 4 分钟）。": "Wake switches to the new name at once, no restart. Your old voiceprint keeps working, but it was recorded with the old name, so the wake rate drops a little; re-record it under Voiceprint (about 4 minutes).",
    "站远一点": "Farther away", "离电脑两三米，或者转过身去说": "Two or three meters from the computer, or turn away while you speak",
    "录着呢，说完停一下就好": "Recording, pause when you're done", "停下": "Stop", "退出录入": "Quit recording",
    // 对话
    "已停止": "Stopped", "想一想…": "Thinking…", "正在想…": "Thinking…", "关掉了详情": "Closed details", "刷新了全部卡片": "Refreshed all cards",
    "锁住了键盘，擦吧": "Keyboard locked, wipe away", "键盘已解锁": "Keyboard unlocked",
    "新对话": "New chat", "收起": "Collapse", "说话": "Talk", "发送": "Send", "关闭": "Close",
    // 管家页（index.html）
    "Mac 管家": "Mac Butler", "每 30 秒刷新本地状态": "Refresh local status every 30 s", "刷新状态": "Refresh", "测显存": "Test VRAM", "DeepSeek 排查": "DeepSeek check",
    "应用": "Apps", "Dock": "Dock", "没有失效图标": "No broken icons", "没有失效图标。": "No broken icons.", "打开": "Open", "退出": "Quit", "在访达中显示": "Show in Finder",
    "CPU": "CPU", "GPU": "GPU", "日志": "Log", "Dock 失效图标": "Broken Dock icons", "其他自建后台服务（LaunchAgent）": "Other custom background services (LaunchAgent)",
    "标签": "Label", "状态": "Status", "上次退出码": "Last exit code", "程序": "Program", "已加载": "loaded", "历次 DeepSeek 排查": "Past DeepSeek checks", "还没跑过。": "Never run.",
    "重启服务": "Restart service", "本地状态服务": "Local status service",
    // 资讯页
    "头版": "Front", "前沿科技": "Frontier", "开源热榜": "Open source", "AI 巨头": "AI labs", "艺术 × 代码": "Art × code", "投资": "Markets",
    "一分钟速览": "One-minute brief", "按重要程度": "by importance", "一分钟速览 · 按重要程度": "One-minute brief · by importance", "头条": "Lead",
    "重编这一期": "Re-edit this issue", "早报进度": "Brief progress", "从头再念": "Read again from the start", "播放早报": "Play the brief", "暂停": "Pause", "这一期没有口播稿。": "This issue has no script.",
    "资讯分栏": "News sections", "上一栏": "Previous section", "下一栏": "Next section", "脑机接口": "BCI", "机器人": "Robotics", "芯片与材料": "Chips & materials", "论文速递": "Papers",
    "过去 24 小时新增 Star": "New stars in the last 24 h", "软件": "Software", "硬件": "Hardware", "模型": "Models", "VESPER 觉得你会想试": "VESPER THINKS YOU'LL WANT TO TRY",
    "每期最多推荐 2 个，只挑和你在折腾的东西有关的。": "At most 2 picks per issue, only things related to what you're tinkering with.", "AI 巨头条线": "AI labs beat", "没新动态的公司自动变淡": "Companies with no news fade out",
    "这两天": "Last two days", "安静": "Quiet", "这一期没有新动态": "Nothing new this issue", "本期封面": "This issue's cover", "生成艺术": "Generative art",
    "配图是按算法现画的示意，不是原作品；点开看原文。": "Illustrations are drawn by an algorithm, not the original work; open the story for the real thing.",
    "持仓": "Holdings", "美股": "US", "港股": "HK", "实时": "Live", "分时图": "Intraday", "纳斯达克": "Nasdaq", "标普 500": "S&P 500", "恒生科技": "Hang Seng Tech", "上证指数": "SSE Composite",
    // Agent 进度（11-09）
    "Agent 进度": "Agent progress", "进行中": "Running", "没动静了": "Quiet", "已停止": "Stopped", "让 Agent 自己报进度": "Let agents report their own progress",
    "现在没有 Agent 在干活": "No agent is working right now", "没有在跑的": "Nothing running", "没装": "Not installed",
    "还没打开。点这里，一键让 Claude Code / Codex / DeepSeek Harness 自己报进度": "Not on yet. Tap here to let Claude Code / Codex / DeepSeek Harness report their own progress",
    "百分比是 Agent 自己估的，可能会往回调；超过 15 分钟没报就显示「没动静了」，一般是在等你回复。": "Percentages are the agent's own estimate and may go down; after 15 minutes without a report it shows “Quiet”, usually because it is waiting for you.",
    "Agent 进度（让 Claude Code / Codex / DeepSeek 自己报进度）": "Agent progress (let Claude Code / Codex / DeepSeek report progress)",
    "在设置里填好 DeepSeek key，管家会自动把进度整理成好懂的话": "Add a DeepSeek key in Settings and the butler will tidy up progress into plain words automatically",
    "管家（DeepSeek）正在整理…": "DeepSeek is tidying this up…", 
    "从会话日志看到的，百分比按它列的计划清单算": "Seen in the session log; percent is from its own plan checklist",
    "从会话日志看到的，没有自报百分比": "Seen in the session log; no self-reported percent",
    "也看会话日志": "Also read session logs", "开关前就在跑的任务也能看到": "shows tasks that started before the switch",
    "「也看会话日志」：Deck 只读 Codex / Claude Code 自己写在本机的会话日志，看最近 20 分钟在干什么，不改任何东西；没有百分比（除非它列了计划清单）。管家（DeepSeek）整理时会用到任务开头的一句话和最近几步。": "“Also read session logs”: Deck only reads the logs Codex / Claude Code already write on this Mac to see the last 20 minutes of work, and changes nothing. There is no percent unless the agent made a plan checklist. When DeepSeek tidies things up it sees the task’s first line and the last few steps.",
    "好了：Codex / Claude Code 正在跑的任务也会显示": "Done: tasks already running in Codex / Claude Code will show too", "不再看会话日志了": "Stopped reading session logs",
    "额度暂时读不到": "Can’t read usage right now", "点开看": "Tap to see", "现在没有进行中的会话": "No active sessions right now",
"（没写这一步在做什么）": "(no step description)",
    "超过 15 分钟没报进度，可能在等你回复": "No report for 15 minutes, may be waiting for you",
    "美股温度": "US market mood", "为什么相关：": "Why it matters: ", "连接长桥": "Connect Longbridge", "会影响你自选的科技资讯": "Tech news that may move your watchlist", "还没有报纸": "No paper yet", "正在编…": "Editing…"
  };
  // 属性（placeholder / title / aria-label）
  var A = {
    "问点什么，或者说「打开系统卡片」": "Ask anything, or “open GitHub”",
    "给 Vesper 发消息": "Message Vesper", "需要注意的事": "Things that need attention", "整理卡片": "Arrange cards", "全部刷新": "Refresh all",
    "搜仓库名、说明、语言": "Search repos, descriptions, languages", "搜仓库": "Search repos", "搜应用": "Search apps", "排序": "Sort",
    "比如 Vesper": "e.g. Vesper", "写个中文谐音，比如 维斯帕；或英文 vest per": "Write how it sounds, e.g. vest per", "每晚自动清": "Clean nightly"
  };

  // ---------- 规则：带数字的句子 ----------
  var UNIT = { "秒": "s", "分钟": "min", "小时": "h", "天": "d", "个月": "mo" };
  // 刘海小 / 中号面板放不下长句：用短写法
  function narrow() { return ctxEl && ctxEl.closest ? (ctxEl.closest("html.ns-s") ? "s" : ctxEl.closest("html.ns-m") ? "m" : "") : ""; }
  function reset(long, short) { var n = narrow(); return n === "s" ? "\u21bb " + short : n === "m" ? "resets " + short : "resets in " + long; }
  function ago(n, u) { return u === "秒" ? n + "s ago" : n + " " + UNIT[u] + " ago"; }
  var WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], WZ = "日一二三四五六", MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  // ---------- Windows 版（11-08 第二步）新出现的界面文字 ----------
  var DW = {
    "连不上 Deck": "Can't reach Deck",
    "语音服务没在运行（Deck 会自动重开，几分钟后还这样就告诉 Claude）": "the voice service isn't running (Deck restarts it automatically; if it stays like this for a few minutes, tell Claude)",
    "确认电脑没睡眠、Deck 在运行（右下角托盘里有 Deck 图标），平板和电脑在同一个 Wi-Fi。": "Make sure the PC is awake and Deck is running (Deck icon in the system tray), and the tablet is on the same Wi-Fi.",
    "每 3 秒刷新，数据和「任务管理器」同源。「退出」相当于正常退出（未保存的内容可能丢失），「强制」会立刻结束；系统进程和别的用户的进程不能结束。Vesper 只在你打开这张卡时看一次，1 分钟内不重复花钱。":
      "Refreshes every 3 s, same source as Task Manager. “Quit” is a normal quit (unsaved work may be lost); “Force” ends it at once. System processes and other users' processes can't be ended. Vesper looks once when you open this card and won't spend again within 1 minute.",
    "数据都在你的电脑上采集；页面每 2 秒拿一次缓存好的结果，没人看时会自动停采。": "All data is collected on your PC; the page fetches cached results every 2 s, and collection stops when nobody is looking.",
    "包括缓存、AppData 和隐藏文件夹（比如": "Includes caches, AppData and hidden folders (e.g. ",
    "语音模型": "Voice models", "语音模型下好了": "Voice models are ready", "正在准备下载…": "Getting ready to download…", "先不下了": "Not now",
    "按住 Ctrl+Alt+J 说话": "Hold Ctrl+Alt+J to talk",
    "声纹模型还没下载：在 设置 → Vesper 的声音 里点「下载语音模型」（约 375MB）。": "The voiceprint model isn't downloaded yet: go to Settings → Vesper's voice and click \u201cDownload voice models\u201d (about 375 MB).",
    // 电脑操控（第三步）
    "电脑操控": "Computer control", "允许 Vesper 操作电脑": "Let Vesper use this PC",
    "打开后 Vesper 才能替你点按钮、打字、按快捷键、开关应用；删除、发送、付款、关机这类会先问你。急停：": "When on, Vesper can click buttons, type, press shortcuts and open or quit apps for you; deleting, sending, paying or shutting down always asks first. Emergency stop: ",
    "，操作时连按 3 次 Esc 也能停。右下角托盘里也能开关。": ", or press Esc three times while it's acting. You can also switch this from the system tray.",
    // 显示方式 / 顶部面板（第三步）
    "窗口": "Window", "顶部面板": "Top panel", "顶部面板大小": "Top panel size", "顶部面板里卡片怎么排": "Cards in the top panel",
    "卡片（拖动、调大小、添加网页在窗口模式里做）": "Cards (drag, resize and add web pages in Window mode)",
    "接着第二块屏时铺在那块屏上，没接就从屏幕顶上弹出。现在用的是": "Fills the second screen when one is plugged in, drops down from the top of the screen otherwise. Now using the ",
    "普通窗口，Ctrl+Alt+空格呼出或收起。现在用的是": "A normal window; Ctrl+Alt+Space shows or hides it. Now using the ",
    "鼠标在屏幕顶边正中停一下就弹出，Ctrl+Alt+空格也能呼出，点别处收起。现在用的是": "Drops down when the pointer rests at the top centre of the screen, or press Ctrl+Alt+Space; click elsewhere to close. Now using the ",
    "。右下角托盘的 Deck 图标里也能切。": ". You can also switch from the Deck icon in the system tray.",
    "显示方式、全屏、开机自启：点右下角托盘里的 Deck 图标。": "Display mode, full screen, launch at login: click the Deck icon in the system tray.",
    "电脑实时状态": "PC Live Status", "电脑管家": "PC Butler", "全部正常": "All good", "按时间": "By time", "不确定": "unsure",
    "Deck 自己：主页、管家、工具箱、Vesper 都在这个程序里": "Deck itself: dashboard, butler, toolbox and Vesper all live in this app",
    "自建后台任务（任务计划程序）": "Custom background tasks (Task Scheduler)", "任务栏失效图标": "Broken Taskbar icons", "任务栏": "Taskbar",
    "没有失效图标": "No broken icons", "没有失效图标。": "No broken icons.", "回收站": "Recycle Bin", "资源管理器": "File Explorer",
    "在资源管理器中显示": "Show in File Explorer", "这台电脑": "This PC",
    // 清理 / 垃圾规则
    "下载里 30 天前的安装包（.exe / .msi）": "Installers in Downloads older than 30 days (.exe / .msi)",
    "临时文件夹里一周没动过的东西（正在用的不动）": "Items in the Temp folder untouched for a week (in-use ones skipped)",
    "开发工具的下载缓存（uv、pip、npm、pnpm、Yarn 等，用到会自动重新下载）": "Dev tool download caches (uv, pip, npm, pnpm, Yarn…; re-downloaded when needed)",
    "Edge 网页缓存（只在 Edge 完全关掉时清）": "Edge web cache (only when Edge is fully closed)",
    "Chrome 网页缓存（只在 Chrome 没打开时清）": "Chrome web cache (only when Chrome is closed)",
    "临时文件夹": "Temp folder", "Windows 更新下载缓存（系统自己管，不清）": "Windows Update download cache (managed by Windows, not cleaned)",
    "应用自动更新留下的旧安装文件": "Old installer files left by app auto-updates",
    "临时文件夹里 7 天没动过的东西（%TEMP%）": "Items in %TEMP% untouched for 7 days",
    "程序崩溃时留下的转储文件（14 天前的）": "Crash dump files (older than 14 days)", "Windows 错误报告（30 天前的）": "Windows Error Reports (older than 30 days)",
    "缩略图缓存（资源管理器会重新生成；正在用的跳过）": "Thumbnail cache (File Explorer rebuilds it; in-use files skipped)",
    "没下完的下载": "Unfinished downloads", "下载文件夹里 30 天前的安装包（exe / msi / msix）": "Installers in Downloads older than 30 days (exe / msi / msix)",
    "90 天没动的项目里的依赖和编译产物（node_modules、.next、target…，需要时重新安装）": "Dependencies and build output in projects idle for 90 days (node_modules, .next, target…; reinstall when needed)",
    "开发工具的下载缓存（pip、npm、uv、yarn、electron 等，用到会自动重新下载）": "Dev tool download caches (pip, npm, uv, yarn, electron…; re-downloaded when needed)",
    "系统垃圾": "System junk", "开发": "Dev", "每日清理清单": "Daily cleanup list", "只在云端，没下载到本机": "Cloud-only, not downloaded",
    // 磁盘面板
    "文件夹": "Folder", "临时文件": "Temp files", "你自己的临时文件夹，应用退出或清理时多半会删": "Your own temp folder; apps usually clear it on exit or cleanup",
    "应用商店应用的数据": "Store app data", "应用（32 位）": "Apps (32-bit)", "应用数据和缓存": "App data and caches", "文档": "Documents", "图片": "Pictures",
    "视频": "Videos", "音乐": "Music", "缩略图缓存 · 可以清": "Thumbnail cache · safe to clean", "网页缓存 · 可以清": "Web cache · safe to clean",
    "崩溃转储 · 可以清": "Crash dumps · safe to clean", "着色器缓存 · 可以清": "Shader cache · safe to clean", "显卡驱动": "Graphics driver",
    "浏览数据和缓存": "Browsing data and caches", "全局安装的 Node 包": "Globally installed Node packages", "设置和缓存": "Settings and caches",
    "虚拟磁盘和镜像": "Virtual disks and images", "网盘（只在云端的文件不占空间）": "Cloud drive (cloud-only files take no space)", "下载": "Downloads",
    "从网上下载的": "Downloaded from the web", "Windows 截图": "Windows screenshot", "Windows 录屏": "Windows screen recording",
    "文件太多，只看了一部分。": "Too many files; only part was checked.", "系统盘": "System disk",
    "正在找最近 7 天新放进来的文件…（第一次要十几秒）": "Looking for files added in the last 7 days… (first time takes 10+ s)",
    "Windows 记的是应用的全部写入（含网络和设备），会比真正写硬盘的多一些。": "Windows counts all writes by an app (including network and devices), so this is a bit higher than real disk writes.",
    "包括缓存、AppData 和隐藏文件夹（比如 .codex）。": "Includes caches, AppData and hidden folders (like .codex).",
    "这台电脑读不到每个进程的 GPU 占用": "This PC can't read per-process GPU usage",
    // 工具箱
    "能用 winget 更新的应用": "Apps winget can update", "Microsoft Store": "Microsoft Store", "打开 Microsoft Store": "Open Microsoft Store",
    "winget 的输出": "winget output", "当前用户": "Current user", "所有用户": "All users", "运行库（别的软件要用）": "Runtime (other apps need it)",
    "系统组件": "System component", "Deck 自己": "Deck itself", "这个应用不允许卸载": "This app can't be uninstalled",
    "没有剩下要清的残留。": "No leftovers to clean.", "残留移进了回收站，可以撤销。": "Leftovers moved to the Recycle Bin; you can undo.",
    "已卸载": "Uninstalled", "按住 Esc 3 秒": "Hold Esc for 3 seconds", "倒计时结束自动解锁": "Unlocks when the countdown ends",
    "在这里点「解锁」，或对 Vesper 说「解锁键盘」": "Click “Unlock” here, or tell Vesper “unlock the keyboard”",
    "按 Ctrl+Alt+Del 或锁屏时自动解锁（系统保留的组合键，任何程序都挡不住）": "Ctrl+Alt+Del or locking the screen unlocks (reserved by Windows; no app can block it)",
    "连鼠标一起锁": "Lock the mouse too", "锁着时只能用按住 Esc、Ctrl+Alt+Del 或倒计时解锁，最多 2 分钟": "While locked, only holding Esc, Ctrl+Alt+Del or the countdown unlocks; 2 min max",
    "按住 Esc 解锁": "held Esc", "电源键和 Ctrl+Alt+Del 是系统保留的，软件挡不住，擦到那里轻一点；按到时会自动解锁。": "The power button and Ctrl+Alt+Del are reserved by Windows and can't be blocked; wipe gently there. Pressing them unlocks.",
    "Windows 版暂时没有黑屏，只锁了键盘": "No black screen on Windows yet; only the keyboard is locked", "Windows 版暂时没有黑屏，只锁了键盘和鼠标": "No black screen on Windows yet; keyboard and mouse are locked",
    "这台电脑没有 winget（应用安装程序），没法自动查更新。可以在 Microsoft Store 里装「应用安装程序」。": "This PC has no winget (App Installer), so updates can't be checked. Install “App Installer” from the Microsoft Store.",
    "问 winget 有哪些能更新…": "Asking winget what can be updated…", "Windows 版不需要额外授权": "No extra permission needed on Windows",
    "用 winget（Windows 自带的应用安装程序）查哪些应用能更新，Microsoft Store 的应用也算。第一次要几十秒。": "Uses winget (Windows' built-in App Installer) to find apps with updates, including Microsoft Store apps. The first check takes tens of seconds.",
    "临时文件、崩溃转储、错误报告、浏览器缓存、开发工具缓存、没下完的下载、旧项目的依赖目录，外加「每日清理」的那份缓存清单（每晚 23:30 自动跑）。只移进回收站。": "Temp files, crash dumps, error reports, browser caches, dev tool caches, unfinished downloads, old project dependencies, plus the Daily Cleanup cache list (runs nightly at 23:30). Recycle Bin only.",
    "需要管理员权限，请在资源管理器里自己处理": "Needs admin rights; handle it yourself in File Explorer", "这是一个链接，不动它": "This is a link; left alone",
    "Windows 版还没做这个功能，后面几步会接上。": "Not available on Windows yet; coming in a later step.",
    // 进程页（Mac 版也用）
    "强制": "Force", "系统进程": "System process", "受保护的系统进程": "Protected system process", "系统": "system", "Deck 后台服务": "Deck backend", "Deck 语音服务": "Deck voice service", "Deck 在用": "Used by Deck",
    "没有 DeepSeek key": "No DeepSeek key", "Deck 自己的窗口": "Deck's own window"
  };
  for (var kw in DW) if (!(kw in D)) D[kw] = DW[kw];

  var R = [
    // 进程名「python（server.py）」：英文界面换半角括号
    [/^([A-Za-z0-9_.\- ]+)（([A-Za-z0-9_.\- \/]+)）$/, "$1 ($2)"],
    [/^CPU 主要被 (.+) 占着$/, "CPU is mostly used by $1"],
    [/^(.+) · PID (\d+) · 系统$/, "$1 · PID $2 · system"],
    [/^数据已经 (.+) 没更新了$/, function (m, t) { return "No update since " + tr(t); }],
    [/^语音唤醒出问题了：(.+)$/, function (m, t) { return "Voice wake has a problem: " + t.split("；").map(tr).join("; "); }],
    // 磁盘「谁在变大」第一次量完（「1 小时」在单独的 span 里）
    [/^已经量过一次（(.+?)），下一次量完（(.+?)）就能比出谁在涨。(先选)?$/, function (m, a, b, c) { return "Measured once (" + tr(a) + "). After the next run (" + tr(b) + ") you'll see what's growing." + (c ? " Pick " : ""); }],
    [/^已经量过一次（(.+?)），下一次量完就能比出谁在涨。(先选)?$/, function (m, a, c) { return "Measured once (" + tr(a) + "). After the next run you'll see what's growing." + (c ? " Pick " : ""); }],
    // Windows 语音模型（第三步）
    [/^唤醒、听你说话、朗读要用到的语音模型还没装好，下载一次就行（约 (\d+)MB，从 k2-fsa 官方下载，只存在这台电脑上）。$/, "The voice models for wake word, listening and speech aren't installed yet. Download them once (about $1 MB, from the official k2-fsa releases; kept on this PC only)."],
    [/^下载没完成：(.*)（下次接着下）。唤醒、听你说话、朗读要用到的语音模型还没装好，下载一次就行（约 (\d+)MB，从 k2-fsa 官方下载，只存在这台电脑上）。$/, "Download didn't finish: $1 (it resumes next time). The voice models still need about $2 MB."],
    [/^下载语音模型（约 (\d+)MB）$/, "Download voice models (about $1 MB)"],
    [/^正在下载语音模型 (.*?) ?(\d+)%$/, "Downloading voice models $1 $2%"], [/^正在解压 (.*)$/, "Unpacking $1"], [/^正在校验 (.*)$/, "Verifying $1"],
    [/^(\d+) ?(秒|分钟|小时|天|个月)前$/, function (m, n, u) { return ago(n, u); }],
    [/^(?:约 ?)?(\d+)[ \u00a0]?(秒|分钟|小时|天)后$/, function (m, n, u) { return "in " + n + (u === "秒" ? " s" : " " + UNIT[u]); }],
    [/^(\d+)月(\d+)日 周(.)$/, function (m, mo, d, w) { return WD[WZ.indexOf(w)] + ", " + MON[mo - 1] + " " + d; }],
    [/^(\d+) 项需要注意$/, function (m, n) { return narrow() ? n + (n === "1" ? " alert" : " alerts") : n + (n === "1" ? " needs" : " need") + " attention"; }],
    [/^(\d+) 项需要\.\.\.$|^(\d+) 项需要…$/, function (m, a, b) { return (a || b) + " need…"; }],
    [/^磁盘还剩 (.+)$/, "$1 free"], [/^磁盘还剩 \.\.\.$|^磁盘还剩 …$/, "Free …"],
    [/^每小时少 (.+)$/, "−$1 per hour"], [/^剩 ([\d.]+ ?[KMGT]?B)$/, "$1 left"],
    [/^外置 (.+)$/, "External $1"],
    [/^(\d+) ?天 ?(\d+) ?小时后重置$/, function (m, a, b) { return reset(a + " d " + b + " h", a + "d " + b + "h"); }],
    [/^(\d+) ?小时 ?(\d+) ?分后重置$/, function (m, a, b) { return reset(a + " h " + b + " min", a + "h " + b + "m"); }],
    [/^(\d+) ?分钟后重置$/, function (m, a) { return reset(a + " min", a + "m"); }], [/^(\d+) ?小时后重置$/, function (m, a) { return reset(a + " h", a + "h"); }],
    [/^(\d+) 个会话在跑 ·$/, "$1 sessions running ·"], [/^(\d+) 个会话在跑 · 今天 (.+) token$/, "$1 sessions running · $2 tokens today"],
    [/^今天 (.+) token$/, "$1 tokens today"], [/^本周用了 (.+) token$/, "$1 tokens this week"],
    [/^余额 · (.+)$/, function (m, t) { return "Balance · " + tr(t); }],
    [/^人民币欠 (.+)$/, "CNY owed $1"], [/^合计约 (.+)$/, "Total ≈ $1"], [/^欠 (.+)$/, "owes $1"],
    [/^(\d+) ?小时前移走 (.+)，可一键放回$/, "Moved $2 $1 h ago, undoable"],
    [/^(\d+) ?分钟前移走 (.+)，可一键放回$/, "Moved $2 $1 min ago, undoable"],
    [/^个自制应用正常 · (\d+) 个在运行$/, "own apps OK · $1 running"], [/^个自制应用正常 · (\d+) 个在运行 · (\d+) 个没开$/, "own apps OK · $1 running · $2 off"],
    [/^(.+)：近 7 天崩溃 (\d+) 次$/, function (m, a, n) { return a + ": crashed " + n + "× in 7 days"; }],
    [/^近 7 天崩溃 (\d+) 次$/, "crashed $1× in 7 days"],
    [/^(.+)：日志 (.+)$/, "$1: log $2"], [/^日志 (.+)$/, "log $1"],
    [/^(.+)：应该常驻，但现在没在运行$/, "$1: should stay running but isn't"],
    [/^(.+)：CI 失败$/, "$1: CI failed"],
    [/^(\d+) 个仓库 · (\d+) 星$/, "$1 repos · $2 stars"], [/^本周 (\d+) 次浏览$/, "$1 views this week"],
    [/^(\d+) 个应用能更新$/, "$1 app updates"],
    [/^上次检查 (.+)$/, function (m, t) { return "Last checked " + tr(t); }],
    [/^更新于 (.+) · 每 (\d+) (秒|分钟) ?自动更新$/, function (m, t, n, u) { return "Updated " + tr(t) + " · every " + n + (u === "秒" ? " s" : " min"); }],
    [/^下次在 (\d+) 小时后$/, "next in $1 h"], [/^下次在 (\d+) 分钟后$/, "next in $1 min"],
    [/^(\d+) 分钟前自动移走的 (\d+) 项$/, "$2 items auto-moved $1 min ago"], [/^(\d+) 小时前自动移走的 (\d+) 项$/, "$2 items auto-moved $1 h ago"],
    [/^(\d+) 小时前清了 (\d+) 项$/, "Cleared $2 items $1 h ago"], [/^(\d+) 小时前清了$/, "Cleared $1 h ago:"], [/^(\d+) 分钟前清了$/, "Cleared $1 min ago:"],
    [/^近 (\d+) 天$/, "Last $1 d"],
    [/^AI 工具旧记录 · (\d+) 项$/, "Old AI tool records · $1"], [/^AI 工具旧记录 ·$/, "Old AI tool records ·"], [/^(\d+) 项$/, "$1 items"],
    [/^近 7 天 (.+)$/, "7 d $1"], [/^近 30 天 (.+)$/, "30 d $1"],
    [/^(.+) → 废纸篓$/, "$1 → Trash"],
    [/^还剩（共 (.+)）$/, "free (of $1)"],
    [/^1 个进程 · 最近 10 分钟写了$/, "1 process · written in last 10 min"], [/^(\d+) 个进程 · 最近 10 分钟写了$/, "$1 processes · written in last 10 min"],
    [/^开着以来 (.+)$/, "$1 since start"],
    [/^(\d+) 秒$/, "$1 s"], [/^(\d+) 分钟$/, "$1 min"],
    [/^上次清洁 (\d+) 分钟前结束（倒计时到了）$/, "Last cleaning ended $1 min ago (timer ran out)"],
    [/^第 (\d+) \/ (\d+) 步$/, "Step $1 / $2"],
    [/^至少要：「(.+)」类 (\d+) 遍、句子 (\d+) 句；英文要么跳过，要么录 (\d+) 遍以上。$/, function (m, a, b, c, d) { return "At least: “" + tr(a) + "” " + b + "×, " + c + " sentences; English either skipped or " + d + "× or more."; }],
    [/^你好(\S+)$/, "Hey $1"],
    [/^说「你好(\S+)」或「Hey (\S+)」叫醒 · (\d+) 种叫法$/, "Say “Hey $2” to wake it · $3 variants"],
    [/^已录好 · (.+)$/, "Recorded · $1"],
    [/^没装：(.+) 不存在$/, "Not installed: $1 not found"], [/^找不到 LaunchAgent (.+)$/, "LaunchAgent $1 not found"], [/^本地服务 (.+) 无响应$/, "Local service $1 not responding"],
    [/^(.+) 下一期：(早版|午版|晚版) (\d+:\d+)。$/, function (m, a, n, t) { return tr(a) + " Next: " + ({ "早版": "Morning", "午版": "Noon", "晚版": "Evening" })[n] + " " + t + "."; }],
    [/^最近 5 小时 (.+) · 本周 (.+)$/, "Last 5 h $1 · this week $2"],
    [/^额度是 (.+)的$/, function (m, t) { return "Quota from " + tr(t); }],
    [/^额度来自 Claude Code 自己的 \/usage（和你在 Claude Code 里看到的一样，不花 token），每 3 分钟查一次，(.+)更新。$/, function (m, t) { return "Quota comes from Claude Code's own /usage (same numbers you see in Claude Code, costs no tokens), checked every 3 min, updated " + tr(t) + "."; }],
    [/^额度来自 Claude Code 自己给状态栏的数据，(.+)更新。$/, function (m, t) { return "Quota comes from Claude Code's status-line data, updated " + tr(t) + "."; }],
    [/^额度来自用量接口，(.+)更新。$/, function (m, t) { return "Quota comes from the usage API, updated " + tr(t) + "."; }],
    [/^本周 (\S+)$/, "This week $1"],
    [/^进行中的会话 · (\d+) 个 claude 进程$/, function (m, n) { return "Active sessions · " + n + " claude process" + (n === "1" ? "" : "es"); }],
    [/^没找到 (.+) 的 API key（(.+) 里的 (.+)）$/, "No $1 API key found ($3 in $2)"],
    [/^(.+)：没装：(.+) 不存在$/, "$1: not installed: $2 not found"], [/^(.+)：本地服务 (.+) 无响应$/, "$1: local service $2 not responding"],
    [/^(.+) · 上次用 (.+)$/, function (m, a, t) { return a + " · used " + tr(t); }],
    [/^(\d+) 个应用。点「卸载…」先看看会移走哪些东西，不会马上动。$/, "$1 apps. Tap “Uninstall…” to preview what would be moved; nothing happens right away."],
    [/^(.+) · 剩 (.+)$/, "$1 · $2 free"], [/^(.+)（外置）$/, "$1 (external)"],
    [/^每小时量一次 · 上次 (.+)$/, function (m, t) { return "Measured hourly · last " + tr(t); }],
    [/^涨得最快 (.+)$/, "Fastest growing $1"], [/^没移动成功：(.+)（(.+)）$/, "Couldn't move: $1 ($2)"],
    [/^说「你好(\S+)」或「Hey (\S+)」叫醒$/, "Say “Hey $2” to wake it"],
    [/^第 (\d+) 步 (.+?)（已录）$/, function (m, n, t) { return "Step " + n + " " + tr(t) + " (recorded)"; }],
    [/^第 (\d+) 步 (.+)$/, function (m, n, t) { return "Step " + n + " " + tr(t); }],
    [/^约 (.+) · (\d+) 次$/, "≈ $1 · $2 calls"],
    [/^缓存命中 (.+)$/, "cache hit $1"], [/^未命中 (.+)$/, "cache miss $1"], [/^输出 (.+)$/, "output $1"],
    [/^token 按每次对话 DeepSeek 返回的用量记账；花费按官方价估算（每百万 token：$/, "Tokens are counted from what DeepSeek reports for each chat; cost is estimated at list price (per million tokens: "],
    [/^）。从 (.+) 开始记。也可以直接问 Vesper「你用了多少」。监控台上的 DeepSeek 卡片显示的是 DeepSeek Harness 的，不含这里。$/, "). Counting since $1. You can also just ask Vesper “how much have you used”. The DeepSeek card on the dashboard shows a different key, not this one."],
    [/^联网：搜索 (\d+) 次、读网页 (\d+) 次，约 \$(.+)（DeepSeek 官方价，成功一次 \$0\.00075）$/, "Web: $1 searches, $2 page reads, ≈ $$$3 (DeepSeek list price, $0.00075 per success)"],
    [/^(.+) · 运行中 (.+)$/, function (m, v, t) { return v + " · running " + t; }],
    [/^(.+) · 未运行 · 在 Dock$/, "$1 · not running · in Dock"], [/^(.+) · 运行中 (.+) · 在 Dock$/, "$1 · running $2 · in Dock"], [/^(.+) · 未运行$/, "$1 · not running"],
    [/^本地状态更新于 (.+)$/, "Local status updated $1"],
    [/^空闲 (\d+%)$/, "$1 free"], [/^显存在用 (.+)$/, "$1 VRAM in use"], [/^(\d+) \/ (\d+) 在运行$/, "$1 / $2 running"],
    [/^(\d+) 个需要留意$/, "$1 need a look"], [/^在「(.+)」文件夹里$/, "in the “$1” folder"], [/^服务 (\d+)$/, "service $1"],
    [/^最近 7 天 · 点一行在资源管理器里显示$/, "Last 7 days · click a row to show in File Explorer"], [/^找不到计划任务 (.+)$/, "Scheduled task $1 not found"],
    [/^(.+)（([A-Z]):）$/, "$1 ($2:)"], [/^([A-Z]) 盘$/, "$1: drive"], [/^临时文件夹 (\d+)GB$/, "Temp folder $1 GB"], [/^(\d+) 个图标$/, "$1 icons"],
    [/^最近推送 (.+)$/, function (m, t) { return "Pushed " + tr(t); }],
    [/^今天 (.+)$/, "today $1"],
    [/^(\d+) 个 PR$/, "$1 PR"], [/^(\d+) 天没动$/, "idle $1 d"],
    [/^(.+) · (.+) · 上次用 (\d+) ?(秒|分钟|小时|天)前$/, function (m, a, b, n, u) { return a + " · " + b + " · used " + ago(n, u); }],
    [/^(.+) · 没记录用过$/, "$1 · never used"],
    [/^第 (\d+) 期$/, "No. $1"], [/^(\d+) 条$/, "$1"], [/^盘后 (.+)$/, "After hours $1"], [/^(\S+) ▲ 利好$/, "$1 ▲ positive"], [/^(\S+) ▼ 利空$/, "$1 ▼ negative"], [/^(\S+) 中性$/, "$1 neutral"],
    [/^(.+) · (早版|午版|晚版)$/, function (m, a, b) { return a + " · " + ({ "早版": "Morning", "午版": "Noon", "晚版": "Evening" })[b]; }],
    [/^DeepSeek · 还没有记忆$/, "DeepSeek · no memories yet"], [/^DeepSeek · 记得 (\d+) 件事$/, "DeepSeek · remembers $1 things"],
    [/^Vesper 的记忆 · 还没有记忆$/, "Vesper's memory · none yet"], [/^Vesper 的记忆 · 记得 (\d+) 件事$/, "Vesper's memory · $1 things"], [/^第 (\d+) 页$/, "Page $1"], [/^晚版 · (.+) 编排$/, "Evening · edited $1"], [/^午版 · (.+) 编排$/, "Noon · edited $1"], [/^早版 · (.+) 编排$/, "Morning · edited $1"],
    [/^读了 (\d+) 条$/, "read $1"], [/^(\d+) 年 (\d+) 月 (\d+) 日 星期(.)$/, function (m, y, mo, d, w) { return ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][WZ.indexOf(w)] + ", " + ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][mo - 1] + " " + d + ", " + y; }],
    [/^(早版|午版|晚版) (\d+:\d+)$/, function (m, n, t) { return ({ "早版": "Morning", "午版": "Noon", "晚版": "Evening" })[n] + " " + t; }],
    [/^· 下一期 (.+)$/, "· next $1"], [/^今天 ¥(.+) \/ ¥(.+)$/, "spent today ¥$1 / ¥$2"],
    [/^DeepSeek (.+) 编排 · 本期约 ¥(.+) · 来源 (\d+) 个$/, "Edited by DeepSeek at $1 · this issue ≈ ¥$2 · $3 sources"], [/^(\d+) 个来源$/, "$1 sources"],
    [/^(.+) 详情$/, function (m, a) { return tr(a) + " details"; }], [/^(.+) 准确数值$/, function (m, a) { return "Exact " + tr(a); }],
    [/^移除 (.+)$/, function (m, a) { return "Remove " + tr(a); }], [/^显示 (.+)$/, function (m, a) { return "Show " + tr(a); }],
    [/^现在没有在跑的会话 ·$/, "No sessions running ·"], [/^内存用了 (\d+%)$/, "Memory $1 used"], [/^CPU 用了 (\d+%)$/, "CPU $1 used"],
    [/^磁盘用了 (\d+%)$/, "Disk $1 used"], [/^GPU 用了 (\d+%)$/, "GPU $1 used"],
    // 对话里的操作小标签
    [/^播放「(.+)」$/, "Playing \u201c$1\u201d"], [/^接着播「(.+)」$/, "Resumed \u201c$1\u201d"], [/^暂停了「(.+)」$/, "Paused \u201c$1\u201d"],
    [/^打开了「(.+)」$/, "Opened \u201c$1\u201d"], [/^刷新了「(.+)」$/, "Refreshed \u201c$1\u201d"], [/^显示方式改成「(.+)」$/, "Display set to \u201c$1\u201d"],
    [/^在访达里打开了「(.+)」$/, "Opened \u201c$1\u201d in Finder"],
    [/^(\d+) 个在跑$/, "$1 running"], [/^(\d+) 个在跑 · (\d+) 个刚完成$/, "$1 running · $2 just done"], [/^没有在跑的 · (\d+) 个刚完成$/, "Nothing running · $1 just done"],
    [/^(.+) · 还有 (\d+) 个$/, function (m, a, n) { return tr(a) + " · " + n + " more"; }],
    [/^不到 1 分钟$/, "< 1 min"], [/^(\d+) 分钟$/, "$1 min"], [/^(\d+) 小时 (\d+) 分$/, "$1 h $2 min"],
    [/^已用 不到 1 分钟$/, "Under 1 min"], [/^已用 (\d+) 分钟$/, "$1 min so far"], [/^已用 (\d+) 小时 (\d+) 分$/, "$1 h $2 min so far"],
    [/^(Claude Code|Codex|DeepSeek Harness) 报进度$/, function (m, a) { return a + " progress reports"; }],
    [/^管家（DeepSeek）(.+)整理( · 有新进展，稍后自动更新)?$/, function (m, a, b) { return "Tidied up by DeepSeek " + tr(a) + (b ? " · new progress, updating soon" : ""); }],
    [/^(\d+) 分钟前完成$/, "Done $1 min ago"], [/^刚刚完成$/, "Just done"], [/^(\d+) 秒前完成$/, "Done $1 s ago"], [/^(\d+) 分钟前停止$/, "Stopped $1 min ago"], [/^刚刚停止$/, "Just stopped"],
    [/^(.+) 装好了：新开的会话就会报进度$/, "$1 is set up: new sessions will report progress"], [/^已关掉 (.+) 的进度上报，撤掉了 Deck 加的那一项$/, "Turned off progress reporting for $1 and removed what Deck added"],
    [/^打开后，Deck 会给这个 Agent 装一个只在本机用的小工具[\s\S]*$/, function () { return "When on, Deck gives this agent a small local-only tool, report_progress: while working, the agent reports how far along it is and what step it is on. No network, it never reads your conversations, and it costs almost no tokens. Only new sessions pick it up. Turning it off takes effect at once and removes only what Deck added; your own settings are untouched."; }],
    [/^做法借鉴开源项目 Codex Pacer（MIT）：(.+)$/, "Approach borrowed from the open-source Codex Pacer (MIT): $1"],
    // 兜底：「名字：问题」（管家页、主页问题清单把应用名和每条问题拼在一起）——两半各自翻，放最后，前面的专门规则优先
    [/^([^：\n]{1,40})：([^：\n]+)$/, function (m, a, b) { return tr(a) + ": " + tr(b); }]
  ];

  function tr(s) {
    var t = s.trim(); if (!t || !CJK.test(t)) return osFix(s);
    if (t.indexOf("\n") >= 0) return s.split("\n").map(tr).join("\n");
    var nb = /[\u00a0\u202f]/.test(t);
    if (nb) t = t.replace(/[\u00a0\u202f]/g, " ");
    var out = D[t];
    if (out != null && CTX[t] && ctxEl && ctxEl.closest && ctxEl.closest(CTX[t][0])) out = CTX[t][1];
    if (out == null) {
      for (var i = 0; i < R.length; i++) { var r = R[i]; if (r[0].test(t)) { out = typeof r[1] === "function" ? t.replace(r[0], r[1]) : t.replace(r[0], r[1]); break; } }
    }
    if (out == null) { miss[t] = 1; return osFix(s); }
    out = osFix(out);
    if (nb && out.length < 40) out = out.replace(/ /g, "\u00a0");
    var lead = s.match(/^\s*/)[0], trail = s.match(/\s*$/)[0];
    return lead + out + trail;
  }
  var miss = {}, ctxEl = null;
  // 同一个中文词在不同位置意思不同：[选择器, 在这个选择器里时用的译法]
  var CTX = { "更新": [".seg, [role=tablist]", "Updates"], "Mac 实时状态": ["html.ns-s main#grid *", "Mac Status"] };
  api.t = tr; api.missing = function () { return Object.keys(miss); };
  api.add = function (d, a) { var k; for (k in d || {}) D[k] = d[k]; for (k in a || {}) A[k] = a[k]; };
  api.rule = function (re, rep) { R.unshift([re, rep]); };

  var ATTRS = ["placeholder", "title", "aria-label"];
  function trAttr(el) {
    for (var i = 0; i < ATTRS.length; i++) {
      var v = el.getAttribute && el.getAttribute(ATTRS[i]);
      if (v && CJK.test(v)) { var o = A[v.trim()] != null ? A[v.trim()] : tr(v); if (o !== v) el.setAttribute(ATTRS[i], o); }
    }
  }
  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { var p = root.parentNode; if (p && !/^(SCRIPT|STYLE|TEXTAREA)$/.test(p.nodeName)) { ctxEl = p; var o = tr(root.nodeValue); ctxEl = null; if (o !== root.nodeValue) root.nodeValue = o; } return; }
    if (root.nodeType !== 1 && root.nodeType !== 11) return;
    if (root.nodeType === 1) { if (/^(SCRIPT|STYLE|TEXTAREA)$/.test(root.nodeName)) return; if (root.hasAttribute("data-no-i18n")) return; trAttr(root); }
    var w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT), n;
    while ((n = w.nextNode())) {
      if (n.nodeType === 3) { var pp = n.parentNode; if (pp && !/^(SCRIPT|STYLE|TEXTAREA)$/.test(pp.nodeName) && !(pp.closest && pp.closest("[data-no-i18n]"))) { ctxEl = pp; var oo = tr(n.nodeValue); ctxEl = null; if (oo !== n.nodeValue) n.nodeValue = oo; } }
      else trAttr(n);
    }
  }
  function start() {
    document.documentElement.lang = "en";
    walk(document.body);
    if (document.title && CJK.test(document.title)) document.title = tr(document.title);
    new MutationObserver(function (ms) {
      for (var i = 0; i < ms.length; i++) {
        var m = ms[i];
        if (m.type === "characterData") walk(m.target);
        else if (m.type === "attributes") trAttr(m.target);
        else for (var j = 0; j < m.addedNodes.length; j++) walk(m.addedNodes[j]);
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
})();
