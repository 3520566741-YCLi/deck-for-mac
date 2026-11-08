/* 工具箱面板：卸载应用（连同残留）、垃圾清理、文件管理、应用更新、键盘清洁。
   后端在 manager/apptools.py。规矩：先列清单给你看，勾好后点按钮、再点一次确认，才移进废纸篓；不直接删除任何东西。
   这个文件只在本机打开监控台时加载（iPad 上只显示卡片摘要）。 */
(function () {
  "use strict";
  var H = window.DeckHost;
  if (!H) return;
  var esc = H.esc, fmtB = H.fmtB, $ = H.$;
  var S = { tab: "apps", apps: null, q: "", sort: "name", plan: null, result: null, busy: "", junk: null, files: null, space: null,
            updates: null, upJob: null, arm: null, minMb: 200, err: "", openD: {},
            clean: null, cOpt: { seconds: 90, black: true, mouse: false } };
  var TABS = [["apps", "卸载应用"], ["junk", "垃圾清理"], ["files", "文件"], ["updates", "更新"], ["clean", "小工具"]];

  function api(op, body, cb) {
    H.post("/api/tools/" + op, body || {}, function (code, r) {
      if (code !== 200 || !r) r = { error: code === 403 ? "没有权限（只能在这台 Mac 上操作）" : "管家没响应，稍后再试" };
      cb(r);
    });
  }
  // 后台任务：每秒问一次进度，直到做完
  function job(op, body, onProg, cb) {
    api(op, body, function (r) {
      if (r.error || !r.job) return cb(r);
      (function tick() {
        api("job", { id: r.job }, function (j) {
          if (j.state === "running") { onProg(j.progress || ""); return setTimeout(tick, 1000); }
          cb(j.result || j);
        });
      })();
    });
  }
  function open() { return H.openKey() === "__tools"; }
  function draw() { if (open()) render(); }
  function ago(ts) { return ts ? H.ago(ts) : ""; }
  function days(d) { return d == null ? "" : d < 1 ? "今天" : d < 60 ? d + " 天前" : Math.round(d / 30) + " 个月前"; }
  function btn(t, label, attrs, cls) { return '<button class="btn sm ' + (cls || "") + '" data-press data-t="' + t + '"' + (attrs || "") + ">" + label + "</button>"; }
  function note(t, cls) { return '<p class="note ' + (cls || "") + '">' + t + "</p>"; }
  function errBox() { return S.err ? note(esc(S.err), "warn-t") : ""; }
  // 危险按钮要点两次：第一次变成「再点一次确认」，3 秒内有效
  function armed(key) { return S.arm && S.arm.k === key && Date.now() < S.arm.until; }
  function arm(key) { S.arm = { k: key, until: Date.now() + 3000 }; draw(); setTimeout(function () { if (S.arm && S.arm.k === key && Date.now() >= S.arm.until) { S.arm = null; draw(); } }, 3100); }

  function render() {
    $("sheetTitle").textContent = "工具箱";
    $("sheetAction").innerHTML = "";
    var seg = '<div class="seg" role="tablist">' + TABS.map(function (t) {
      return '<button role="tab" data-t="tab" data-v="' + t[0] + '" aria-pressed="' + (S.tab === t[0]) + '">' + t[1] + "</button>";
    }).join("") + "</div>";
    var body = S.tab === "apps" ? appsView() : S.tab === "junk" ? junkView() : S.tab === "files" ? filesView() : S.tab === "clean" ? cleanView() : updatesView();
    var html = seg + errBox() + body + (S.tab === "clean" ? "" : note("所有东西只会移进废纸篓，不会直接删除。清空废纸篓之前，都能在这里一键撤销放回原处。", "muted"));
    var el = $("sheetBody");
    var y = S.top ? 0 : el.scrollTop, focus = document.activeElement && document.activeElement.id;
    el.innerHTML = html; el.__html = null; el.scrollTop = y; S.top = false;
    if (focus === "tbq") { var q = $("tbq"); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
    if (S.tab === "apps" && !S.apps && !S.busy) loadApps();
    if (S.tab === "clean" && !S.clean && S.busy !== "clean") cleanLoad();
    if (S.tab === "updates" && !S.updates && !S.busy) api("updates/last", {}, function (r) { S.updates = r.at ? r : {}; draw(); });
  }

  // ------------------------------------------------ 卸载应用
  function loadApps() {
    S.busy = "apps";
    api("apps", {}, function (r) { S.busy = ""; S.apps = r.apps || []; S.err = r.error || ""; draw(); });
  }
  function appsView() {
    if (S.plan && S.plan.kind === "uninstall") return planView();
    if (S.result && S.result.kind === "uninstall") return resultView();
    if (!S.apps) return note("正在列出应用…");
    var q = S.q.toLowerCase();
    var apps = S.apps.filter(function (a) { return !q || a.name.toLowerCase().indexOf(q) >= 0 || (a.bid || "").toLowerCase().indexOf(q) >= 0; });
    apps.sort(S.sort === "size" ? function (a, b) { return (b.bytes || 0) - (a.bytes || 0); }
            : S.sort === "used" ? function (a, b) { return (a.last_used || 0) - (b.last_used || 0); }
            : function (a, b) { return a.name.localeCompare(b.name, "zh"); });
    var bar = '<div style="display:flex;gap:8px;align-items:center"><input id="tbq" type="search" placeholder="搜应用" value="' + esc(S.q) +
      '" style="flex:1;min-height:36px;border-radius:10px;border:1px solid var(--line);background:transparent;color:inherit;padding:0 10px;font:inherit">' +
      '<select data-t="sort" aria-label="排序" style="min-height:36px;border-radius:10px;background:transparent;color:inherit;border:1px solid var(--line);padding:0 6px">' +
      [["name", "按名字"], ["size", "按大小"], ["used", "最久没用的在前"]].map(function (o) { return '<option value="' + o[0] + '"' + (S.sort === o[0] ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") +
      "</select></div>";
    var rows = apps.map(function (a) {
      var tags = (a.running ? '<span class="badge">开着</span>' : "") + (a.mas ? '<span class="badge" style="background:var(--glass-hi);color:var(--text2)">App Store</span>' : "") +
        (a.brew ? '<span class="badge" style="background:var(--glass-hi);color:var(--text2)">Homebrew</span>' : "");
      var sub = [a.version, a.bytes != null ? fmtB(a.bytes) : "", a.last_used ? "上次用 " + ago(a.last_used) : "没记录用过"].filter(Boolean).join(" · ");
      return '<div class="li" style="min-height:44px"><span class="t"><b style="font-weight:600">' + esc(a.name) + "</b> " + tags +
        '<br><small class="muted">' + esc(sub) + "</small></span>" +
        (a.protected ? '<span class="r">' + esc(a.protected) + "</span>" : btn("plan", "卸载…", ' data-path="' + esc(a.path) + '"')) + "</div>";
    });
    return bar + note(S.apps.length + " 个应用。点「卸载…」先看看会移走哪些东西，不会马上动。", "muted") +
      (rows.length ? '<div class="list">' + rows.join("") + "</div>" : '<div class="empty">没有匹配的应用</div>');
  }
  function planView() {
    var p = S.plan, sel = 0, n = 0;
    p.items.forEach(function (it) { if (it.on && !it.admin) { sel += it.bytes; n++; } });
    var head = p.kind === "uninstall" ? '<div class="hero"><b class="num">' + fmtB(sel) + "</b><small>卸载「" + esc(p.app.name) + "」会移进废纸篓</small></div>" +
      (p.app.running ? note("它现在开着，要先退出才能卸载。" + " " + btn("quit", "退出它", ' data-path="' + esc(p.app.path) + '"'), "warn-t") : "") +
      (p.app.brew ? note("它是用 Homebrew 装的，清单里有 brew 的安装记录，一起移走后 brew 就不再认为它装着。", "muted") : "") +
      (p.app.mas ? note("它来自 App Store，以后想用可以在 App Store 的「已购项目」里重新下载。", "muted") : "")
      : '<div class="hero"><b class="num">' + fmtB(sel) + "</b><small>勾选的文件会移进废纸篓</small></div>";
    var rows = p.items.map(function (it) {
      var dis = it.admin ? " disabled" : "";
      return '<label class="li" style="min-height:40px;align-items:flex-start;padding-top:6px"><input type="checkbox" data-t="pick" data-i="' + it.i + '"' + (it.on && !it.admin ? " checked" : "") + dis +
        ' style="margin-top:3px"><span class="t" style="white-space:normal;word-break:break-all"><span class="mono" style="font-size:12.5px">' + esc(it.path) + "</span><br><small class=\"muted\">" +
        esc(it.why + (it.age_days != null ? " · " + days(it.age_days) + "用过" : "")) + (it.admin ? " · 需要管理员权限，不会自动处理" : "") + "</small></span>" +
        '<span class="r">' + fmtB(it.bytes) + "</span>" + '<button class="btn sm" data-t="reveal" data-path="' + esc(it.path) + '" aria-label="在访达中显示" style="min-height:28px;padding:0 8px">显示</button></label>';
    }).join("");
    var go = armed("exec") ? "再点一次：移到废纸篓" : "移到废纸篓（" + n + " 项）";
    return head + (p.note ? note(esc(p.note), "muted") : "") + '<div class="list">' + rows + "</div>" +
      '<div style="display:flex;gap:8px;flex-wrap:wrap">' + btn("exec", S.busy === "exec" ? "正在移动…" : go, n && !S.busy ? "" : " disabled", "primary") + btn("cancel", "不了") + "</div>";
  }
  function resultView() {
    var r = S.result, ok = r.count != null;
    return '<div class="hero"><b class="num">' + (ok ? fmtB(r.bytes) : "–") + "</b><small>" + (ok ? "已移进废纸篓，共 " + r.count + " 项" : "没有完成") + "</small></div>" +
      (r.error ? note(esc(r.error), "warn-t") : "") +
      (r.failed && r.failed.length ? '<div class="sec">没移动的</div><div class="list">' + r.failed.map(function (f) {
        return '<div class="li"><span class="dot warn"></span><span class="t">' + esc(f.path || f) + '</span><span class="r">' + esc(f.why || "") + "</span></div>"; }).join("") + "</div>" : "") +
      '<div style="display:flex;gap:8px">' + (r.run_id && r.count ? btn("undo", r.undone ? "已放回原处" : "撤销，放回原处", r.undone ? " disabled" : "") : "") + btn("done", "好") + "</div>";
  }

  // ------------------------------------------------ 键盘清洁（后端 manager/kbclean.py，拦按键的是 DeckHands 清洁模式）
  var cTimer = null;
  function cleanLoad() {
    S.busy = "clean";
    api("clean/status", {}, function (r) { S.busy = ""; cleanSet(r); });
  }
  // 服务器时间和本机时间差一点也没关系：倒计时按服务器给的 until - now 算，再用本机时钟往下走
  function cleanSet(r) {
    if (r && r.now) r.skew = Date.now() / 1000 - r.now;
    S.clean = r || {};
    if (r && r.error && r.state !== "locked") S.cErr = r.error; else if (r && r.state === "locked") S.cErr = "";
    cleanWatch(); draw();
  }
  function cleanLeft() {
    var c = S.clean;
    return c && c.state === "locked" ? Math.max(0, Math.ceil(c.until - (Date.now() / 1000 - (c.skew || 0)))) : 0;
  }
  function mmss(n) { return Math.floor(n / 60) + ":" + ("0" + (n % 60)).slice(-2); }
  // 锁着的时候：每秒刷新倒计时，每 2 秒问一次服务器（在 Mac 上用按钮、⌘⌘ 或 Vesper 解锁的，这里也要马上变回来）
  function cleanWatch() {
    var on = S.clean && S.clean.state === "locked";
    if (on && !cTimer) {
      var n = 0;
      cTimer = setInterval(function () {
        if (!open() || S.tab !== "clean") return;
        var el = $("kcLeft"); if (el) el.textContent = mmss(cleanLeft());
        if (++n % 2 === 0 || cleanLeft() <= 0) api("clean/status", {}, function (r) { if (r && !r.error) cleanSet(r); });
      }, 1000);
    } else if (!on && cTimer) { clearInterval(cTimer); cTimer = null; }
  }
  function sw(k, label, sub) {
    return '<div class="row-btn kc-row"><span class="t" style="flex:1;min-width:0">' + label + (sub ? '<br><small class="muted">' + sub + "</small>" : "") +
      '</span><button class="switch" role="switch" data-t="copt" data-v="' + k + '" aria-checked="' + !!S.cOpt[k] + '" aria-label="' + label + '"></button></div>';
  }
  function cleanView() {
    var c = S.clean;
    var intro = note("不动文件的随手小工具，以后新加的也放在这一页。", "muted");
    if (!c) return intro + note("正在看键盘的状态…");
    var locked = c.state === "locked";
    var ways = [
      [!c.mouse || !locked, "屏幕上的「按住解锁」按住 1 秒"],
      [true, "同时按住左右两个 ⌘ 2 秒（中间别碰别的键）"],
      [true, "倒计时结束自动解锁"],
      [true, "在这里点「解锁」，或对 Vesper 说「解锁键盘」"],
      [true, "合盖、睡眠、锁屏时自动解锁"]];
    var waysHtml = '<details data-d="kc-ways"' + (("kc-ways" in S.openD ? S.openD["kc-ways"] : locked) ? " open" : "") + '><summary class="note">怎么解锁（任何一种都行）</summary>' +
      '<div class="group" style="padding:4px 12px;margin-top:6px"><div class="list">' +
      ways.filter(function (w) { return w[0]; }).map(function (w) { return '<div class="li"><span class="dot ok"></span><span class="t">' + w[1] + "</span></div>"; }).join("") + "</div></div></details>";
    var errHtml = S.cErr ? '<div class="kc-sec kc-err">' + note(esc(S.cErr), "warn-t") +
      (/辅助功能/.test(S.cErr) ? "<div>" + btn("cset", "打开辅助功能设置") + "</div>" : "") + "</div>" : "";
    var head = '<div class="kc-head"><span class="kc-ic">' + H.icon("keyboard") + '</span><span class="kc-tt"><b>擦键盘</b><small>' +
      (locked ? "键盘锁着" + (c.black ? " · 黑屏" : "") + (c.mouse ? " · 触控板也锁着" : "") : "锁住以后按什么都没反应，擦完再解锁") + "</small></span>" +
      (locked ? '<span class="badge kc-on">锁定中</span>' : "") + "</div>";
    var body;
    if (locked) {
      var full = c.max && c.until >= c.max - 1;
      body = '<div class="kc-sec kc-count"><b class="num" id="kcLeft">' + mmss(cleanLeft()) + "</b><small>后自动解锁</small></div>" +
        '<div class="kc-sec kc-act">' + btn("cstop", S.busy === "cstop" ? "正在解锁…" : "解锁", S.busy ? " disabled" : "", "primary kc-go") +
        btn("cmore", full ? "已到 10 分钟上限" : "再给 30 秒", full || S.busy ? " disabled" : "") + "</div>";
    } else {
      var durs = (c.durations || [30, 60, 90, 120, 300]).filter(function (d) { return !S.cOpt.mouse || d <= 120; });
      if (durs.indexOf(S.cOpt.seconds) < 0) S.cOpt.seconds = durs[durs.length - 1];
      var seg = '<div class="seg" role="radiogroup" aria-label="锁多久">' + durs.map(function (d) {
        var on = S.cOpt.seconds === d;
        return '<button role="radio" data-t="cdur" data-v="' + d + '" aria-pressed="' + on + '" aria-checked="' + on + '">' + (d < 60 || d % 60 ? d + "\u00a0秒" : d / 60 + "\u00a0分钟") + "</button>";
      }).join("") + "</div>";
      var why = { button: "按住按钮解锁", "cmd+cmd": "按 ⌘⌘ 解锁", timer: "倒计时到了", deck: "在 Deck 里解锁", "sleep/lock": "睡眠或锁屏时自动解锁", "screen locked": "锁屏时自动解锁", watchdog: "到点兜底解锁" }[c.last_reason];
      var last = why && c.last_at && Date.now() / 1000 - c.last_at < 1800 ? '<small class="muted kc-last">上次清洁 ' + ago(c.last_at) + "结束（" + why + "）</small>" : "";
      body = (c.installed === false ? '<div class="kc-sec">' + note("没找到 DeckHands.app，键盘清洁靠它拦按键。在 Mac 上运行 manager/hands/build.sh 装一下。", "warn-t") + "</div>" : "") +
        '<div class="kc-sec"><div class="kc-lab">锁多久</div>' + seg + "</div>" +
        sw("black", "黑屏", "灰尘和指纹看得更清楚，也方便顺手擦屏幕") +
        sw("mouse", "连触控板一起锁", "锁着时只能用 ⌘⌘ 或倒计时解锁，最多 2 分钟") +
        '<div class="kc-sec kc-act">' + btn("cgo", S.busy === "cgo" ? "正在锁…" : "开始清洁", S.busy || c.installed === false ? " disabled" : "", "primary kc-go") + last + "</div>";
    }
    return intro + '<div class="gtitle">清洁</div><div class="group kc-card">' + head + errHtml + body + "</div>" + waysHtml +
      note("电源键和 Touch ID 直连硬件，软件挡不住，擦到那里轻一点；按到让 Mac 睡眠时会自动解锁。", "muted");
  }
  // 原生锁屏面板跟着 Deck 的皮肤和白天/夜里走
  function look() {
    var r = document.documentElement, th = r.getAttribute("data-theme");
    var dark = th ? th === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    return { skin: r.classList.contains("skin-soft") ? "soft" : "glass", theme: dark ? "dark" : "light" };
  }

  // ------------------------------------------------ 垃圾清理
  function junkView() {
    if (S.result && S.result.kind === "junk") return resultView();
    var intro = note("参考 Mole 的分类：应用缓存、日志、崩溃报告、没下完的下载、Xcode 编译缓存、旧项目的依赖目录，外加「每日清理」的那份缓存清单（每晚 23:30 自动跑）。只移进废纸篓。", "muted");
    if (!S.junk) return intro + btn("junkscan", S.busy === "junk" ? "正在扫描… " + esc(S.prog || "") : "扫描", S.busy ? " disabled" : "", "primary");
    var p = S.junk, sel = 0;
    p.rules.forEach(function (r) { if (r.pick) sel += r.bytes; });
    var groups = {}, order = [];
    p.rules.forEach(function (r) { if (!groups[r.group]) { groups[r.group] = []; order.push(r.group); } groups[r.group].push(r); });
    var html = order.map(function (g) {
      return '<div class="gtitle">' + esc(g) + '</div><div class="group" style="padding:4px 12px">' + groups[g].map(function (r) {
        return '<label class="li" style="min-height:40px"><input type="checkbox" data-t="rule" data-v="' + esc(r.id) + '"' + (r.pick ? " checked" : "") +
          '><span class="t" style="white-space:normal">' + esc(r.label) + "</span><span class=\"r\">" + fmtB(r.bytes) + "</span></label>" +
          '<details data-d="junk-' + esc(r.id) + '"' + (S.openD["junk-" + r.id] ? " open" : "") + ' style="padding:0 0 4px 26px"><summary class="note muted" style="cursor:pointer">看看是哪 ' + r.count + " 项</summary>" +
          '<div class="list">' + r.items.map(function (i) {
            return '<div class="li"><span class="t mono" style="font-size:12px">' + esc(i.path) + '</span><span class="r">' + fmtB(i.bytes) + (i.age_days ? " · " + days(i.age_days) : "") + "</span></div>";
          }).join("") + (r.count > r.items.length ? '<div class="empty">还有 ' + (r.count - r.items.length) + " 项…</div>" : "") + "</div></details>";
      }).join("") + "</div>";
    }).join("");
    var go = armed("junkrun") ? "再点一次：移到废纸篓" : "移到废纸篓（" + fmtB(sel) + "）";
    return '<div class="hero"><b class="num">' + fmtB(sel) + "</b><small>勾选的能腾出（清空废纸篓后）</small></div>" +
      note((p.trash_bytes != null ? "废纸篓现在有 " + fmtB(p.trash_bytes) + "。" : "") + "管家不会替你清空废纸篓。", "muted") + (html || '<div class="empty">没找到可以清的东西</div>') +
      '<div style="display:flex;gap:8px;flex-wrap:wrap">' + btn("junkrun", S.busy === "junkrun" ? "正在移动…" : go, sel && !S.busy ? "" : " disabled", "primary") + btn("junkscan", "重新扫描", S.busy ? " disabled" : "") + "</div>";
  }

  // ------------------------------------------------ 文件
  function filesView() {
    if (S.plan && S.plan.kind === "files") return planView();
    if (S.result && S.result.kind === "files") return resultView();
    var sizes = [100, 200, 500, 1000];
    var top = '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="note">找大于</span><select data-t="minmb" style="min-height:36px;border-radius:10px;background:transparent;color:inherit;border:1px solid var(--line);padding:0 6px">' +
      sizes.map(function (m) { return '<option value="' + m + '"' + (S.minMb === m ? " selected" : "") + ">" + (m >= 1000 ? m / 1000 + " GB" : m + " MB") + "</option>"; }).join("") +
      "</select><span class=\"note\">的文件</span>" + btn("filescan", S.busy === "files" ? "正在找…" : "找大文件和重复文件", S.busy ? " disabled" : "", "primary") +
      btn("space", S.busy === "space" ? (S.spaceProg ? "正在统计… " + esc(S.spaceProg) : "正在统计… 最多一分钟") : "看空间分布", S.busy ? " disabled" : "") + "</div>";
    var sp = S.space ? '<div class="sec">家目录里各文件夹占的空间</div><div class="list">' + S.space.map(function (x) {
      var w = S.space[0].bytes ? Math.max(2, x.bytes / S.space[0].bytes * 100) : 0;
      return '<div class="li"><span class="t mono" style="font-size:12.5px">' + esc(x.path) + '</span><span style="width:30%;height:6px;border-radius:3px;background:var(--track)"><i style="display:block;height:100%;border-radius:3px;background:var(--accent);width:' +
        w.toFixed(0) + '%"></i></span><span class="r" style="min-width:64px;text-align:right">' + (x.slow ? "太大没算完" : fmtB(x.bytes)) + "</span></div>"; }).join("") + "</div>" : "";
    return top + note("在家目录里找（不进 ~/Library 和应用包）。找到的东西默认都不勾，你自己挑。", "muted") + sp;
  }

  // ------------------------------------------------ 更新
  function updatesView() {
    var u = S.updates || {};
    var top = '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' + btn("upcheck", S.busy === "updates" ? "正在检查… " + esc(S.prog || "") : "检查更新", S.busy ? " disabled" : "", "primary") +
      (u.at ? '<span class="note muted">上次检查 ' + esc(ago(u.at)) + "</span>" : "") + "</div>";
    if (!u.at) return top + note("参考 Latest：用 Sparkle 更新源和 App Store 查应用；Homebrew 装的用 brew 查。第一次要几十秒。", "muted");
    function sec(title, arr, row) { return arr && arr.length ? (title ? '<div class="sec">' + title + "</div>" : "") + '<div class="list">' + arr.map(row).join("") + "</div>" : ""; }
    function line(name, from, to, right) {
      return '<div class="li" style="min-height:44px"><span class="t"><b style="font-weight:600">' + esc(name) + '</b><br><small class="muted">' + esc((from || "?") + " → " + to) + "</small></span>" + right + "</div>";
    }
    var job = S.upJob;
    var html = top +
      sec("Homebrew 装的应用", u.brew, function (x) {
        var k = "cask:" + x.token, b = job && job.key === k;
        return line(x.token, x.from, x.to, b ? '<span class="r">' + esc(job.msg) + "</span>" : btn("brewup", armed(k) ? "再点一次确认" : "更新", ' data-kind="cask" data-v="' + esc(x.token) + '"' + (job && job.running ? " disabled" : "")));
      }) +
      sec("App Store", u.mas, function (x) { return line(x.name, x.from, x.to, btn("openup", "打开 App Store", ' data-kind="mas" data-v="' + esc(x.id) + '"')); }) +
      sec("应用自带更新（Sparkle）", u.sparkle, function (x) { return line(x.name, x.from, x.to, btn("openup", "打开它去更新", ' data-kind="app" data-v="' + esc(x.path) + '"')); }) +
      ((u.formula || []).length ? '<details data-d="formula"' + (S.openD.formula ? " open" : "") + '><summary class="note" style="cursor:pointer">' + u.formula.length + " 个命令行工具能更新（Homebrew）</summary>" : "") +
      sec("", u.formula, function (x) {
        var k = "formula:" + x.token, b = job && job.key === k;
        return line(x.token, x.from, x.to, b ? '<span class="r">' + esc(job.msg) + "</span>" : btn("brewup", armed(k) ? "再点一次确认" : "更新", ' data-kind="formula" data-v="' + esc(x.token) + '"' + (job && job.running ? " disabled" : "")));
      });
    if ((u.formula || []).length) html += "</details>";
    var none = !(u.brew || []).length && !(u.mas || []).length && !(u.sparkle || []).length && !(u.formula || []).length;
    if (none) html += '<div class="empty">都是最新的</div>';
    if (job && job.output) html += '<details><summary class="note">brew 的输出</summary><pre class="mono" style="white-space:pre-wrap;font-size:11.5px;max-height:220px;overflow:auto">' + esc(job.output) + "</pre></details>";
    html += note("Homebrew 更新应用前，会先把旧版用 APFS 克隆复制一份进废纸篓（几乎不占空间），命令行工具的旧版本也会留着不清。", "muted");
    if ((u.unknown || []).length) html += '<details><summary class="note muted">' + u.unknown.length + " 个应用没法自动查更新（自己更新或没有公开的更新源）</summary><p class=\"note muted\">" + esc(u.unknown.join("、")) + "</p></details>";
    if ((u.errors || []).length) html += '<details><summary class="note warn-t">' + u.errors.length + " 个查询出错</summary><p class=\"note muted\">" + esc(u.errors.join("；")) + "</p></details>";
    return html;
  }

  // ------------------------------------------------ 点按
  function act(t, el) {
    S.err = "";
    if (t === "tab") { S.tab = el.getAttribute("data-v"); S.arm = null; S.top = true; return draw(); }
    if (t === "plan") {
      el.disabled = true; el.textContent = "正在找残留…";
      return api("uninstall/plan", { path: el.getAttribute("data-path") }, function (r) {
        if (r.error) { S.err = r.error; return draw(); }
        r.kind = "uninstall"; S.plan = r; draw();
      });
    }
    if (t === "pick") { var it = S.plan.items[+el.getAttribute("data-i")]; it.on = el.checked; return draw(); }
    if (t === "reveal") return api("reveal", { path: el.getAttribute("data-path") }, function () {});
    if (t === "quit") {
      el.disabled = true;
      return api("quit", { path: el.getAttribute("data-path") }, function (r) {
        if (r.error) S.err = r.error; else { S.plan.app.running = false; H.toast("已经让它退出"); }
        draw();
      });
    }
    if (t === "cancel") { if (S.plan) api("cancel", { id: S.plan.id }, function () {}); S.plan = null; return draw(); }
    if (t === "exec") {
      if (!armed("exec")) return arm("exec");
      S.arm = null; S.busy = "exec"; draw();
      var picks = S.plan.items.filter(function (i) { return i.on && !i.admin; }).map(function (i) { return i.i; });
      var kind = S.plan.kind;
      return api("execute", { id: S.plan.id, picks: picks }, function (r) {
        S.busy = "";
        if (r.running) { S.err = r.error; S.plan.app.running = true; return draw(); }
        S.plan = null; r.kind = kind; S.result = r;
        if (kind === "uninstall") S.apps = null;
        if (kind === "files") S.files = null;
        draw();
      });
    }
    if (t === "undo") {
      el.disabled = true;
      return api("undo", { run_id: S.result.run_id }, function (r) {
        if (r.error) S.err = r.error; else { S.result.undone = true; H.toast("放回了 " + r.restored + " 项" + (r.skipped && r.skipped.length ? "，" + r.skipped.length + " 项原位置已有东西没放" : "")); }
        S.apps = null; draw();
      });
    }
    if (t === "done") { S.result = null; return draw(); }
    if (t === "copt") { var k = el.getAttribute("data-v"); S.cOpt[k] = !S.cOpt[k]; return draw(); }
    if (t === "cdur") { S.cOpt.seconds = +el.getAttribute("data-v"); return draw(); }
    if (t === "cset") return api("clean/settings", {}, function () {});
    if (t === "cgo") {
      S.busy = "cgo"; S.cErr = ""; draw();
      var lk = look();
      return api("clean/start", { seconds: S.cOpt.seconds, black: S.cOpt.black, mouse: S.cOpt.mouse, skin: lk.skin, theme: lk.theme }, function (r) { S.busy = ""; cleanSet(r); if (r.state === "locked") H.toast("键盘锁好了，放心擦"); });
    }
    if (t === "cstop") {
      S.busy = "cstop"; draw();
      return api("clean/stop", {}, function (r) { S.busy = ""; cleanSet(r); if (r.state !== "locked") H.toast("键盘已解锁"); });
    }
    if (t === "cmore") return api("clean/extend", {}, function (r) { cleanSet(r); });
    if (t === "junkscan") {
      S.busy = "junk"; S.junk = null; S.prog = ""; draw();
      return job("junk/scan", {}, function (p) { S.prog = p; }, function (r) {
        S.busy = "";
        if (r.error) { S.err = r.error; return draw(); }
        r.rules.forEach(function (x) { x.pick = x.on; }); S.junk = r; draw();
      });
    }
    if (t === "rule") { S.junk.rules.forEach(function (r) { if (r.id === el.getAttribute("data-v")) r.pick = el.checked; }); return draw(); }
    if (t === "junkrun") {
      if (!armed("junkrun")) return arm("junkrun");
      S.arm = null; S.busy = "junkrun"; draw();
      var ids = S.junk.rules.filter(function (r) { return r.pick; }).map(function (r) { return r.id; });
      return api("junk/run", { id: S.junk.id, rules: ids }, function (r) {
        S.busy = ""; S.junk = null; r.kind = "junk"; S.result = r; draw();
      });
    }
    if (t === "filescan") {
      S.busy = "files"; draw();
      return job("files/scan", { min_mb: S.minMb }, function () {}, function (r) {
        S.busy = "";
        if (r.error) { S.err = r.error; return draw(); }
        (r.dups || []).forEach(function (g, gi) {
          g.forEach(function (i, k) { var it = r.items[i]; it.why = (k === 0 ? "重复文件第 " + (gi + 1) + " 组（最早的一份）" : "重复文件第 " + (gi + 1) + " 组（和别的一份内容完全一样）"); });
        });
        r.kind = "files"; S.plan = r; draw();
      });
    }
    if (t === "space") {
      S.busy = "space"; draw();
      S.spaceProg = "";
      return job("space", {}, function (pg) { if (pg !== S.spaceProg) { S.spaceProg = pg; draw(); } }, function (r) { S.busy = ""; S.spaceProg = ""; if (r.error) S.err = r.error; else S.space = r; draw(); });
    }
    if (t === "upcheck") {
      S.busy = "updates"; S.prog = ""; draw();
      return job("updates/check", {}, function (p) { S.prog = p; draw(); }, function (r) {
        S.busy = ""; if (r.error) S.err = r.error; else S.updates = r; draw();
      });
    }
    if (t === "openup") {
      return api("updates/open", { kind: el.getAttribute("data-kind"), ref: el.getAttribute("data-v") }, function (r) {
        if (r.error) { S.err = r.error; draw(); } else if (r.note) H.toast(r.note);
      });
    }
    if (t === "brewup") {
      var kind2 = el.getAttribute("data-kind"), tok = el.getAttribute("data-v"), key = kind2 + ":" + tok;
      if (!armed(key)) return arm(key);
      S.arm = null; S.upJob = { key: key, running: true, msg: "开始…" }; draw();
      return job("updates/brew", { kind: kind2, token: tok }, function (p) { S.upJob.msg = p; draw(); }, function (r) {
        S.upJob = { key: key, running: false, msg: r.ok ? "已更新" + (r.backup ? "，旧版在废纸篓" : "") : (r.error || "没成功"), output: r.output };
        if (r.ok) {
          ["brew", "formula"].forEach(function (k) { if (S.updates[k]) S.updates[k] = S.updates[k].filter(function (x) { return !(x.token === tok && (k === "brew") === (kind2 === "cask")); }); });
          H.toast(tok + " 已更新");
        } else S.err = r.error || "更新没成功";
        draw();
      });
    }
  }

  document.addEventListener("click", function (e) {
    if (!open()) return;
    var el = e.target.closest && e.target.closest("[data-t]");
    if (!el || !$("sheetBody").contains(el) || el.tagName === "SELECT" || el.getAttribute("data-t") === "pick" || el.getAttribute("data-t") === "rule") return;
    e.preventDefault();
    act(el.getAttribute("data-t"), el);
  });
  document.addEventListener("change", function (e) {
    if (!open()) return;
    var el = e.target, t = el.getAttribute && el.getAttribute("data-t");
    if (t === "pick" || t === "rule") return act(t, el);
    if (t === "sort") { S.sort = el.value; draw(); }
    if (t === "minmb") { S.minMb = +el.value; }
  });
  document.addEventListener("toggle", function (e) {
    var d = e.target.getAttribute && e.target.getAttribute("data-d");
    if (d && open()) S.openD[d] = e.target.open;
  }, true);
  document.addEventListener("input", function (e) {
    if (open() && e.target.id === "tbq") { S.q = e.target.value; draw(); }
  });

  window.DeckTools = { render: render };
})();
