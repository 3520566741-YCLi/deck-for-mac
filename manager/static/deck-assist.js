/* 设置 →「助手」：改名字、名字体检、引导式声纹录入、唤醒 / 声纹测试。
   后端都在语音服务 :8778（manager/voice/service/assist.py 和 enroll.py），这里只负责画。
   只在本机打开监控台时加载（iPad 上没有麦克风那一路，也不该改名字）。
   皮肤：只用 Deck 现成的 .group / .seg / .btn / .field / .note，新拟态和液态玻璃各自的样式在 dashboard.html 的 .as-* 里。 */
(function () {
  "use strict";
  var H = window.DeckHost;
  if (!H) return;
  var esc = H.esc, $ = H.$;
  var API = "http://127.0.0.1:8778";
  var KEY = "__assist";
  var S = { view: "name", st: null, down: false, err: "", nameIn: null, hintIn: null, check: null, en: null, cur: null,
            test: null, quietSec: 180, busy: "", arm: null, timer: null, last: "", autoNext: null, skip: {} };

  // ------------------------------------------------ 网络
  function call(method, path, body) {
    var ctl = window.AbortController ? new AbortController() : null;
    var t = setTimeout(function () { if (ctl) ctl.abort(); }, 8000);
    return fetch(API + path, { method: method, headers: body ? { "Content-Type": "application/json" } : {},
                               body: body ? JSON.stringify(body) : undefined, signal: ctl ? ctl.signal : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { clearTimeout(t); d.__code = r.status; return d; }); })
      .catch(function () { clearTimeout(t); return { __code: 0, error: "语音服务没在运行（打开 DeckVoice 后再试）" }; });
  }
  function loadState(cb) {
    call("GET", "/assist/state").then(function (d) {
      S.down = d.__code !== 200;
      if (!S.down) {
        S.st = d;
        if (d.namecheck) S.check = d.namecheck;
        if (d.test) S.test = d.test;
        if (BOOT_NAME() !== d.name && window.DeckAName) DeckAName.set(d.name);
      }
      if (cb) cb();
    });
  }
  function BOOT_NAME() { return window.DeckAName ? DeckAName.get() : "Vesper"; }

  // ------------------------------------------------ 小部件
  function open() { return H.openKey() === KEY; }
  function btn(t, label, attrs, cls) { return '<button class="btn sm ' + (cls || "") + '" data-press data-as="' + t + '"' + (attrs || "") + ">" + label + "</button>"; }
  function note(t, cls) { return '<p class="note ' + (cls || "") + '">' + t + "</p>"; }
  function pct(x) { return x == null ? "—" : Math.round(x * 100) + "%"; }
  function armed(k) { return S.arm && S.arm.k === k && Date.now() < S.arm.until; }
  function name() { return S.st ? S.st.name : BOOT_NAME(); }
  function q(s) { return "「" + esc(s) + "」"; }
  function heyOf(n) { return /[一-鿿]/.test(n) ? "" : "或「Hey " + esc(n) + "」"; }

  // ------------------------------------------------ 设置页里的那一块
  function box() {
    var el = $("assistBox");
    if (!el) return;
    paintBox();
    loadState(paintBox);
  }
  function paintBox() {
    var el = $("assistBox");
    if (!el) return;
    var st = S.st, n = name();
    var html = '<div class="gtitle">助手</div><div class="group as-box">';
    if (S.down && !st) {
      html += '<div class="as-row"><span class="as-t"><b>' + esc(n) + '</b><small>语音服务没在运行，打开 DeckVoice 后这里可以改名字、录声纹、做测试</small></span></div></div>';
      el.innerHTML = html;
      return;
    }
    var vp, vpCls;
    if (!st) { vp = "正在看…"; vpCls = ""; }
    else if (!st.enrolled) { vp = "还没录。录好以后只有你的声音叫得醒"; vpCls = "warn"; }
    else if (st.wake_word_changed) { vp = "是按旧名字" + (st.enrolled_name ? " " + esc(st.enrolled_name) + " " : "") + "录的，改名后要重录"; vpCls = "warn"; }
    else { vp = "已录好" + (st.updated ? " · " + esc(String(st.updated).slice(5, 16)) : ""); vpCls = "ok"; }
    html += row("name", '<b class="as-name">' + esc(n) + "</b>", "说" + q("你好" + n) + heyOf(n) + "叫醒" + (st ? " · " + st.phrases + " 种叫法" : ""), "改名字") +
      row("voice", "声纹", '<span class="dot ' + (vpCls === "ok" ? "ok" : vpCls === "warn" ? "warn" : "") + '"></span>' + vp,
          st && st.enrolled && !st.wake_word_changed ? "重录" : "录入", st && (!st.enrolled || st.wake_word_changed)) +
      row("test", "测试", "叫十遍看能醒几次、请别人叫看会不会被冒充、安静听几分钟看会不会误醒", "去测试") + "</div>";
    el.innerHTML = html;
  }
  function row(v, title, sub, label, primary) {
    return '<div class="as-row"><span class="as-t"><span class="as-tt">' + title + "</span><small>" + sub + "</small></span>" +
      '<button class="btn sm' + (primary ? " primary" : "") + '" data-press data-as="go" data-v="' + v + '">' + label + "</button></div>";
  }

  // ------------------------------------------------ 弹层
  var TABS = [["name", "名字"], ["voice", "声纹"], ["test", "测试"]];
  function render(force) {
    var ae0 = document.activeElement;
    // 正在名字框里打字（尤其是中文输入法拼字时）不重画，不然拼到一半会被打断
    if (!force && ae0 && (ae0.id === "asName" || ae0.id === "asHint") && S.last) return;
    $("sheetTitle").textContent = "助手";
    $("sheetAction").innerHTML = "";
    var seg = '<div class="seg" role="tablist">' + TABS.map(function (t) {
      var busy = (S.en && S.en.session && t[0] !== "voice") || (S.test && S.test.running && t[0] !== "test");
      return '<button role="tab" data-as="tab" data-v="' + t[0] + '" aria-pressed="' + (S.view === t[0]) + '"' + (busy ? " disabled" : "") + ">" + t[1] + "</button>";
    }).join("") + "</div>";
    var body;
    if (S.down && !S.st) body = note("语音服务没在运行。打开 DeckVoice（菜单栏的麦克风图标）后再来。", "warn-t");
    else if (!S.st) body = note("正在看助手的状态…");
    else body = S.view === "voice" ? voiceView() : S.view === "test" ? testView() : nameView();
    var html = seg + (S.err ? note(esc(S.err), "warn-t as-err") : "") + body;
    if (html === S.last) return;
    S.last = html;
    var el = $("sheetBody"), y = el.scrollTop, ae = document.activeElement, fid = ae && ae.id, sel = null;
    if (fid && (fid === "asName" || fid === "asHint")) sel = [ae.selectionStart, ae.selectionEnd];
    el.innerHTML = html; el.__html = null; el.scrollTop = y;
    if (fid && $(fid)) { var f = $(fid); f.focus({ preventScroll: true }); if (sel) try { f.setSelectionRange(sel[0], sel[1]); } catch (e) {} }
  }
  function draw(force) { if (open()) render(force); }
  function show(view) {
    if (view) S.view = view;
    S.err = ""; S.last = "";
    H.openSheet(KEY);
    loadState(function () { draw(); if (S.view === "voice") enrollPoll(); });
    tick();
  }

  // ------------------------------------------------ 名字
  function nameView() {
    var st = S.st;
    if (S.nameIn == null) S.nameIn = st.name;
    if (S.hintIn == null) S.hintIn = st.hint || "";
    var changed = S.nameIn.trim() !== st.name || S.hintIn.trim() !== (st.hint || "");
    var html = '<div class="group as-sec">' +
      '<label class="field"><span>名字</span><input id="asName" maxlength="12" autocomplete="off" autocapitalize="words" spellcheck="false" value="' + esc(S.nameIn) + '" placeholder="比如 Vesper"></label>' +
      '<label class="field"><span>读法（可选）</span><input id="asHint" maxlength="24" autocomplete="off" spellcheck="false" value="' + esc(S.hintIn) + '" placeholder="写个中文谐音，比如 维斯帕；或英文 vest per"></label>' +
      note("英文 3–12 个字母（一个词），或 2–4 个汉字。少见、两三个音节的名字最好叫醒；Siri、小爱这类别的助手的名字不能用。", "muted") +
      '<div class="as-act">' + btn("check", S.check && S.check.running ? "正在体检…" : "体检这个名字", S.check && S.check.running ? " disabled" : "") +
      btn("save", S.busy === "save" ? "正在换…" : "用这个名字", changed && !S.busy ? "" : " disabled", "primary") + "</div></div>";
    html += checkView();
    html += '<div class="gtitle">改名以后</div><div class="group as-sec">' +
      note("唤醒马上换成新名字，不用重启。原来的声纹先留着用，但它是按旧名字录的，叫醒率会低一些，最好到「声纹」重录一次（大约 4 分钟）。" +
           (st.name !== st.default ? "随时可以改回 " + esc(st.default) + "。" : "")) + "</div>";
    return html;
  }
  function checkView() {
    var c = S.check;
    if (!c || !c.name) return "";
    var head = '<div class="gtitle">名字体检 · ' + esc(c.name) + "</div>";
    if (c.running) {
      var p = c.total ? Math.round(c.done * 100 / c.total) : 0;
      return head + '<div class="group as-sec">' + note("用 Mac 自带的合成声音把各种叫法说一遍，再混进一些普通聊天，看两条唤醒路能认出几成、会不会认错。大约 1 分钟。") +
        '<div class="as-bar" role="progressbar" aria-valuenow="' + p + '" aria-valuemin="0" aria-valuemax="100"><i style="width:' + p + '%"></i></div>' +
        '<small class="muted num">' + c.done + " / " + (c.total || "…") + "</small></div>";
    }
    if (c.error) return head + '<div class="group as-sec">' + note(esc(c.error), "warn-t") + "</div>";
    var r = c.result;
    if (!r) return "";
    var verdict = { good: ["好叫醒", "ok"], ok: ["还行", "mid"], poor: ["不太好叫醒", "bad"] }[r.verdict] || ["", ""];
    var fa = (r.false_kws || []).length + (r.false_asr || []).length;
    var html = head + '<div class="group as-sec"><div class="as-hero"><b class="num">' + pct(r.either) + '</b><span><small>合成声音能叫醒</small>' +
      '<span class="as-chip ' + verdict[1] + '">' + verdict[0] + "</span></span></div>" +
      '<div class="as-kv"><span>唤醒模型（第一条路）</span><b class="num">' + pct(r.kws) + "</b></div>" +
      (r.asr != null ? '<div class="as-kv"><span>听写认名字（第二条路）</span><b class="num">' + pct(r.asr) + "</b></div>" : "") +
      '<div class="as-kv"><span>普通聊天被认成在叫它</span><b class="num' + (fa ? " warn-t" : "") + '">' + fa + " / " + r.neg_n + "</b></div>";
    if (r.missed && r.missed.length) html += note("这几种叫法合成声音没叫醒：" + r.missed.map(function (w) { return "<span class=\"nobr\">" + q(w) + "</span>"; }).join(" "), "muted");
    if (r.heard && r.heard.length) html += note("听写把名字写成：" + r.heard.map(esc).join("、") + "（改成这个名字时会记下，以后看到这些写法也认）", "muted");
    if (fa) html += note("被认错的：" + (r.false_kws || []).concat(r.false_asr || []).map(q).join(" ") + "。声纹会拦住别人，但你自己聊天时说到可能会误醒。", "warn-t");
    html += note("合成声音只是参考，真人通常更好叫醒。对照：Vesper 只算第一条路时约 72%。" + (r.verdict === "poor" ? "可以在「读法」里写个中文谐音再体检一次，或者换个名字。" : ""), "muted") + "</div>";
    return html;
  }
  function doCheck() {
    S.err = "";
    call("POST", "/assist/namecheck", { name: S.nameIn, hint: S.hintIn }).then(function (d) {
      if (d.__code !== 200) { S.err = d.error || "体检没开始"; return draw(true); }
      S.check = d; draw(true); tick();
    });
  }
  function doSave(n, h, quiet) {
    var old = S.st ? { name: S.st.name, hint: S.st.hint || "" } : null;
    S.busy = "save"; S.err = ""; draw();
    call("POST", "/assist/name", { name: n, hint: h }).then(function (d) {
      S.busy = "";
      if (d.__code !== 200) { S.err = d.error || "没改成功"; return draw(true); }
      S.st = d; S.nameIn = d.name; S.hintIn = d.hint || "";
      if (window.DeckAName) DeckAName.set(d.name);
      if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
      draw(true); paintBox();
      if (!quiet) H.toast("现在叫 " + d.name + " 了" + (d.enrolled ? "，记得重录声纹" : ""), old && old.name !== d.name ? function () { doSave(old.name, old.hint, true); } : null);
    });
  }

  // ------------------------------------------------ 声纹录入（用 8778 的 /enroll/api，和原来的录入页同一套）
  function eapi(op, body) { return call(body === undefined && op === "state" ? "GET" : "POST", "/enroll/api/" + op, body); }
  function enrollPoll() {
    eapi("state").then(function (d) {
      if (d.__code === 200) S.en = d; else if (d.__code === 0) S.down = true;
      draw();
    });
  }
  function steps() { return (S.en && S.en.steps) || []; }
  function curStep() {
    var ss = steps(), rec = S.en && S.en.rec;
    // 正在录的那一步优先（别的窗口点了录音，或者刚打开弹层时录音还在进行）
    if (rec && rec.step && /waiting|recording|processing/.test(rec.phase)) { var r = ss.filter(function (s) { return s.id === rec.step; })[0]; if (r) return r; }
    if (S.cur) { var c = ss.filter(function (s) { return s.id === S.cur; })[0]; if (c) return c; }
    return ss.filter(function (s) { return !s.done && !S.skip[s.id]; })[0] || null;
  }
  function enoughDone() {
    var ss = steps(), w = 0, e = 0, t = 0;
    ss.forEach(function (s) { if (!s.done) return; if (s.kind === "wake") w++; else if (s.kind === "wake_en") e++; else t++; });
    return w >= 4 && t >= 3 && e !== 1;
  }
  function voiceView() {
    var st = S.st, en = S.en;
    if (!en) return note("正在看声纹的状态…");
    if (!en.models_ok) return note("声纹模型还没下载：在 Mac 上运行 manager/voice/service/models/fetch_wake_models.sh。", "warn-t");
    if (en.result) return resultView(en.result);
    if (!en.session) {
      var status = !st.enrolled ? note("还没录声纹。录好以后，只有你的声音叫得醒 " + esc(st.name) + "，别人叫它不理。") :
        st.wake_word_changed ? note("现在的声纹是按旧名字录的。按" + q("你好" + st.name) + "重录一次，叫醒率会回来。", "warn-t") :
        note("声纹已经录好" + (st.updated ? "（" + esc(st.updated) + "）" : "") + "。门槛：中文 " + (st.threshold != null ? st.threshold.toFixed(2) : "—") +
             (st.threshold_en != null ? "、英文 " + st.threshold_en.toFixed(2) : "") + "。想重来可以重新录，旧的会移进废纸篓。");
      var n = steps().length, cj = st.cjk;
      return '<div class="group as-sec">' + status +
        note("一共 " + n + " 步，大约 4 分钟：" + q("你好" + st.name) + " 5 遍（正常两遍、站远、小声、戴耳机），换 3 种中文叫法" +
             (cj ? "" : "，" + q("Hey " + st.name) + " 3 遍和 2 种英文叫法（英文可以跳过）") + "，再读 3 句话。就在平时用它的地方录，有点环境声没关系。", "muted") +
        '<div class="as-act">' + btn("estart", st.enrolled ? "重新录入" : "开始录入", "", "primary") + "</div></div>";
    }
    var s = curStep(), ss = steps(), idx = s ? ss.indexOf(s) + 1 : ss.length;
    var dots = '<div class="as-steps" role="list">' + ss.map(function (x, i) {
      return '<button role="listitem" class="as-step' + (x.done ? " done" : "") + (s && x.id === s.id ? " cur" : "") + (x.optional ? " opt" : "") +
        '" data-as="ejump" data-v="' + esc(x.id) + '" aria-label="第 ' + (i + 1) + " 步 " + esc(x.title) + (x.done ? "（已录）" : "") + '">' + (i + 1) + "</button>";
    }).join("") + "</div>";
    var rec = en.rec || {}, ph = rec.phase, mine = rec.step && s && rec.step === s.id;
    var html = dots;
    if (!s) {
      html += '<div class="group as-sec">' + note("都录好了。点「完成」生成声纹。") + "</div>";
    } else {
      var prompt = s.kind === "sent" ? s.text : (s.say || "你好" + st.name);
      var msg = en.msg && en.msg.step === s.id ? en.msg : null;
      var phTxt = mine && ph === "waiting" ? "在听，开口说吧…" : mine && ph === "recording" ? "录着呢，说完停一下就好" : ph === "processing" ? "正在检查这一遍…" :
        msg ? (msg.ok ? "✓ " + esc(msg.text) : esc(msg.text)) : s.done ? "这一步录过了，可以重录" : "准备好了就点「录这一遍」";
      var lvl = mine && (ph === "waiting" || ph === "recording") ? Math.round((rec.level || 0) * 100) : 0;
      html += '<div class="group as-sec as-rec' + (mine && ph === "recording" ? " live" : "") + '">' +
        '<div class="as-stephd"><small class="muted num">第 ' + idx + " / " + ss.length + " 步</small><b>" + esc(s.title) + "</b>" + (s.optional ? '<span class="as-chip">可跳过</span>' : "") + "</div>" +
        '<div class="as-prompt' + (s.kind === "sent" ? " sent" : "") + '">' + esc(prompt) + "</div>" +
        (s.hint ? note(esc(s.hint), "muted") : "") +
        '<div class="as-meter" aria-hidden="true"><i style="width:' + lvl + '%"></i></div>' +
        '<p class="note as-ph ' + (msg ? (msg.ok ? "ok-t" : "warn-t") : "") + '" role="status" aria-live="polite">' + phTxt + "</p>" +
        '<div class="as-act">' +
        (mine && (ph === "waiting" || ph === "recording") ? btn("ecancel", "停下") :
          btn("erec", ph === "processing" ? "检查中…" : s.done ? "重录这一遍" : "录这一遍", ph === "processing" ? " disabled" : "", "primary kc-go")) +
        (s.optional && !s.done && ph !== "recording" ? btn("eskip", "跳过") : "") + "</div></div>";
    }
    var fin = enoughDone();
    html += '<div class="as-act as-foot">' + btn("efinish", S.busy === "efinish" ? "正在生成…" : "完成", fin && !S.busy && ph !== "recording" ? "" : " disabled", fin ? "primary" : "") +
      btn("eabort", armed("eabort") ? "再点一次退出（这次录的不要了）" : "退出录入", "", armed("eabort") ? "danger armed" : "danger") + "</div>" +
      (fin ? "" : note("至少要：" + q("你好" + st.name) + "类 4 遍、句子 3 句；英文要么跳过，要么录 2 遍以上。", "muted"));
    return html;
  }
  function resultView(r) {
    var weak = (r.weak_takes || []).length;
    var html = '<div class="group as-sec"><div class="as-hero"><b>✓</b><span><small>声纹录好了</small>' +
      '<span class="as-chip ok">只有你叫得醒</span></span></div>' +
      (r.threshold != null ? '<div class="as-kv"><span>中文门槛</span><b class="num">' + Number(r.threshold).toFixed(2) + "</b></div>" : "") +
      (r.en && r.en.threshold != null ? '<div class="as-kv"><span>英文门槛</span><b class="num">' + Number(r.en.threshold).toFixed(2) + "</b></div>" : "") +
      (weak ? note("有 " + weak + " 遍和别的差得比较多（通常是站远或小声那遍），不影响使用。", "muted") : "") +
      (r.heard_as && r.heard_as.length ? note("听写把名字写成：" + r.heard_as.map(esc).join("、") + "，已经记下，第二条唤醒路以后也认这些写法。", "muted") : "") +
      '<div class="as-act">' + btn("gotest", "去测一下", "", "primary") + btn("edone", "好") + "</div></div>";
    return html;
  }
  function eStart() {
    S.err = ""; S.cur = null; S.skip = {};
    eapi("start", {}).then(function (d) { if (d.__code !== 200) S.err = d.error || "没开始"; else S.en = d; draw(); tick(); });
  }
  function eRec(id) {
    S.err = "";
    eapi("record", { step: id }).then(function (d) { if (d.__code !== 200) S.err = d.error || "没开始录"; else S.en = d; draw(); tick(); });
  }
  // 这一遍录好了：一秒后自动跳到下一个没录的步骤
  function maybeAdvance() {
    var en = S.en, s = curStep();
    if (!en || !en.session || !s || !en.msg || en.msg.step !== s.id || !en.msg.ok || (en.rec && en.rec.phase !== "idle")) return;
    if (S.autoNext === en.msg) return;
    S.autoNext = en.msg;
    setTimeout(function () {
      var nx = steps().filter(function (x) { return !x.done && !S.skip[x.id]; })[0];
      if (S.autoNext === en.msg) { S.cur = nx ? nx.id : null; draw(); }
    }, 1000);
  }

  // ------------------------------------------------ 测试
  function testView() {
    var st = S.st, t = S.test;
    var noVp = !st.enrolled ? note("还没录声纹：测试只能看叫不叫得醒，「会不会被冒充」要录完声纹才测得了。", "warn-t") : "";
    if (t && t.running) return runView(t);
    var html = noVp;
    if (t && t.summary) html += summaryView(t);
    var n = esc(st.name);
    html += '<div class="gtitle">选一种测试</div><div class="group as-box">' +
      '<div class="as-row"><span class="as-t"><span class="as-tt">我自己叫 10 遍</span><small>每遍听提示说' + q("你好" + st.name) + heyOf(st.name) +
      "，算能叫醒几次、声纹放行几次。可以换着站远、小声说</small></span>" + btn("tstart", "开始", ' data-v="me"', "primary") + "</div>" +
      '<div class="as-row"><span class="as-t"><span class="as-tt">请别人叫 10 遍</span><small>找家人朋友对着电脑叫 ' + n + "，看声纹能不能把他们都拦住</small></span>" +
      btn("tstart", "开始", ' data-v="other"' + (st.enrolled ? "" : " disabled")) + "</div>" +
      '<div class="as-row as-quiet"><span class="as-t"><span class="as-tt">安静听几分钟</span><small>照常聊天、放视频或音乐，别叫它，看会不会自己醒</small></span>' +
      '<div class="as-qa"><div class="seg">' + [[60, "1 分钟"], [180, "3 分钟"], [300, "5 分钟"]].map(function (o) {
        return '<button data-as="qsec" data-v="' + o[0] + '" aria-pressed="' + (S.quietSec === o[0]) + '">' + o[1] + "</button>";
      }).join("") + "</div>" + btn("tstart", "开始", ' data-v="quiet"') + "</div></div></div>" +
      note("测试时照常在听，但只记结果、不会真的叫醒，也不会拿测试时的声音去学（可能是别人）。", "muted");
    return html;
  }
  function dotOf(r) {
    if (r.miss) return '<i class="as-d miss" title="没叫醒"></i>';
    var sc = r.score != null ? " · " + r.score.toFixed(2) : "";
    // 别人在叫：被声纹拦住才是好事（绿），放行了是坏事（红）
    if (S.test && S.test.mode === "other") return '<i class="as-d ' + (r.ok ? "bad" : "ok") + '" title="' + (r.ok ? "被放行了" : "拦住了") + sc + '"></i>';
    return '<i class="as-d ' + (r.ok ? "ok" : "rej") + '" title="' + (r.ok ? "叫醒了" : "听到了但声纹没过") + sc + '"></i>';
  }
  function runView(t) {
    var st = S.st;
    if (t.mode === "quiet") {
      var left = Math.max(0, t.seconds - t.elapsed_s), live = t.live || {};
      return '<div class="group as-sec as-run"><div class="as-big num">' + mmss(left) + "</div>" +
        note("照常聊天、放视频都行，别叫 " + esc(st.name) + "。") +
        '<div class="as-kv"><span>听到像在叫它</span><b class="num">' + (live.heard || 0) + " 次</b></div>" +
        '<div class="as-kv"><span>声纹也放行了（会误醒）</span><b class="num' + (live.passed ? " warn-t" : "") + '">' + (live.passed || 0) + " 次</b></div>" +
        '<div class="as-act">' + btn("tstop", "提前结束") + "</div></div>";
    }
    var ph = t.phase, last = t.results.length ? t.results[t.results.length - 1] : null;
    var say = "你好" + st.name;
    var line = ph === "ready" ? "准备…" : ph === "speak" ? "现在说" + q(say) : ph === "grace" ? "在认…" :
      ph === "result" && last ? (last.miss ? "这遍没叫醒" : last.ok ? "✓ 叫醒了" + (last.score != null ? " · 声纹 " + last.score.toFixed(2) : "") :
        (t.mode === "other" ? "✓ 拦住了" : "听到了，但声纹没过") + (last.score != null ? " · " + last.score.toFixed(2) : "")) : "";
    var total = { ready: 1400, speak: 4500, grace: 1500, result: 1000 }[ph] || 1000;
    var p = Math.max(0, Math.min(100, 100 - t.left_ms * 100 / total));
    return '<div class="group as-sec as-run' + (ph === "speak" ? " live" : "") + '">' +
      '<div class="as-stephd"><small class="muted">' + (t.mode === "other" ? "请别人叫" : "你自己叫") + '</small><b class="num">第 ' + t.round + " / " + t.rounds + " 遍</b></div>" +
      '<div class="as-prompt" role="status" aria-live="polite">' + line + "</div>" +
      '<div class="as-bar thin"><i style="width:' + (ph === "speak" ? p : ph === "ready" ? 0 : 100) + '%"></i></div>' +
      '<div class="as-dots">' + t.results.map(dotOf).join("") + "</div>" +
      '<div class="as-act">' + btn("tstop", "停下") + "</div></div>";
  }
  function summaryView(t) {
    var s = t.summary, st = S.st;
    if (!s) return "";
    var html = '<div class="gtitle">刚才的结果</div><div class="group as-sec">';
    if (t.mode === "quiet") {
      html += '<div class="as-hero"><b class="num">' + s.passed + '</b><span><small>' + s.minutes + " 分钟里误醒</small>" +
        '<span class="as-chip ' + (s.passed ? "bad" : "ok") + '">' + (s.passed ? (s.per_hour != null ? "约 " + s.per_hour + " 次 / 小时" : "有误醒") : "一次没有") + "</span></span></div>" +
        '<div class="as-kv"><span>听到像在叫它（被声纹拦下的也算）</span><b class="num">' + s.heard + " 次</b></div>" +
        note(s.passed ? "有误醒：多半是聊天里说到了很像名字的词。换个更少见的名字，或者重录声纹让门槛更准。" :
             s.heard ? "名字被听到过，但声纹都拦住了，不会误醒。" : "很安静，没被误叫醒。", s.passed ? "warn-t" : "muted");
    } else if (t.mode === "other") {
      html += '<div class="as-hero"><b class="num">' + s.passed + '</b><span><small>别人叫了 ' + s.rounds + " 遍，被放行</small>" +
        '<span class="as-chip ' + (s.passed ? "bad" : "ok") + '">' + (s.passed ? "有冒充成功" : "全都拦住了") + "</span></span></div>" +
        '<div class="as-dots">' + t.results.map(dotOf).join("") + "</div>" +
        (s.max_score != null ? '<div class="as-kv"><span>别人最高的声纹分（门槛 ' + fmt(st.threshold) + "）</span><b class=\"num\">" + s.max_score.toFixed(2) + "</b></div>" : "") +
        note(s.passed ? "被放行了：建议在安静的地方重录声纹，五遍都用平时的音量。" : s.hits ? "唤醒词被听到了，但声纹都没过，安全。" : "一遍都没触发唤醒，也就谈不上冒充。", s.passed ? "warn-t" : "muted");
    } else {
      var good = s.wake_rate >= 0.8 && (s.pass_rate == null || s.pass_rate >= 0.8);
      html += '<div class="as-hero"><b class="num">' + s.passed + " / " + s.rounds + '</b><span><small>叫醒了几次（声纹也通过）</small>' +
        '<span class="as-chip ' + (good ? "ok" : s.wake_rate >= 0.5 ? "mid" : "bad") + '">' + (good ? "很稳" : s.wake_rate >= 0.5 ? "还行" : "偏难叫醒") + "</span></span></div>" +
        '<div class="as-dots">' + t.results.map(dotOf).join("") + "</div>" +
        '<div class="as-kv"><span>听到在叫它</span><b class="num">' + s.hits + " / " + s.rounds + " · " + pct(s.wake_rate) + "</b></div>" +
        '<div class="as-kv"><span>其中声纹通过</span><b class="num">' + s.passed + " / " + s.hits + " · " + pct(s.pass_rate) + "</b></div>" +
        '<div class="as-kv"><span>两条路各认出</span><b class="num">唤醒模型 ' + s.kws + " · 听写 " + s.asr + "</b></div>" +
        (s.min_score != null ? '<div class="as-kv"><span>你的声纹分（门槛 ' + fmt(st.threshold) + "）</span><b class=\"num\">" + s.min_score.toFixed(2) + " – " + s.max_score.toFixed(2) + "</b></div>" : "");
      var adv = [];
      if (s.wake_rate < 0.7) adv.push("叫醒率偏低：试试在「名字」里写个读法提示，或做一次名字体检换个更好叫的名字");
      if (s.pass_rate != null && s.pass_rate < 0.8) adv.push(st.wake_word_changed || !st.enrolled ? "声纹还没按现在的名字录，先去重录" : "声纹常常没过：在平时用它的地方重录一次");
      html += note(adv.length ? adv.join("；") + "。" : "两条唤醒路和声纹都正常。", adv.length ? "warn-t" : "muted");
    }
    return html + "</div>";
  }
  function fmt(x) { return x == null ? "—" : Number(x).toFixed(2); }
  function mmss(s) { s = Math.round(s); return Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2); }
  function tStart(mode) {
    S.err = "";
    call("POST", "/assist/test/start", { mode: mode, rounds: 10, seconds: S.quietSec }).then(function (d) {
      if (d.__code !== 200) S.err = d.error || "没开始"; else S.test = d;
      draw(); tick();
    });
  }

  // ------------------------------------------------ 轮询：录音/测试/体检时快，平时慢；弹层关了就停
  function tick() {
    clearTimeout(S.timer);
    if (!open()) return;
    var fast = (S.en && S.en.session) || (S.test && S.test.running) || (S.check && S.check.running);
    S.timer = setTimeout(function () {
      if (!open()) return;
      var jobs = [];
      if (S.view === "voice" || (S.en && S.en.session)) jobs.push(eapi("state").then(function (d) { if (d.__code === 200) { S.en = d; S.down = false; } }));
      if (S.test && S.test.running) jobs.push(call("GET", "/assist/test").then(function (d) { if (d.__code === 200) { S.test = d; if (!d.running) loadState(); } }));
      if (S.check && S.check.running) jobs.push(call("GET", "/assist/namecheck").then(function (d) { if (d.__code === 200) S.check = d; }));
      if (!jobs.length) jobs.push(new Promise(function (r) { loadState(r); }));
      Promise.all(jobs).then(function () { maybeAdvance(); draw(); tick(); });
    }, fast ? (S.en && S.en.rec && /waiting|recording/.test(S.en.rec.phase) ? 120 : 300) : 3000);
  }
  // 关掉弹层：正在录音/测试的停下（不留着占麦克风那一路、不一直拦着唤醒）
  function leave() {
    clearTimeout(S.timer);
    // 录入中途关掉：退出这次录入（录入期间唤醒是暂停的，不能让它一直停着）。旧声纹不受影响
    if (S.en && S.en.session && !S.en.result) {
      eapi("abort", {});
      if (steps().some(function (x) { return x.done; })) H.toast("录入已退出，这次录的没保存，原来的声纹还在");
      S.en = null;
    } else if (S.en && S.en.result) { eapi("abort", {}); S.en = null; }
    if (S.test && S.test.running) call("POST", "/assist/test/stop", {});
    S.nameIn = S.hintIn = null; S.last = "";
  }

  // ------------------------------------------------ 事件
  function act(t, el) {
    var v = el.getAttribute("data-v");
    if (t === "go") return show(v);
    if (t === "tab") { S.view = v; S.err = ""; S.last = ""; if (v === "voice") enrollPoll(); draw(); return tick(); }
    if (t === "check") return doCheck();
    if (t === "save") return doSave(S.nameIn, S.hintIn);
    if (t === "estart") return eStart();
    if (t === "erec") { var s = curStep(); if (s) eRec(s.id); return; }
    if (t === "ecancel") return eapi("cancel", {}).then(function (d) { if (d.__code === 200) S.en = d; draw(); });
    if (t === "eskip") { var cs = curStep(); if (cs) S.skip[cs.id] = true; S.cur = null; return draw(); }
    if (t === "ejump") { if (S.en && S.en.rec && /waiting|recording/.test(S.en.rec.phase)) return; S.cur = v; return draw(); }
    if (t === "efinish") {
      S.busy = "efinish"; draw();
      return eapi("finish", {}).then(function (d) {
        S.busy = "";
        if (d.__code !== 200) S.err = d.error || "没生成成功";
        enrollPoll(); loadState(function () { draw(); paintBox(); });
      });
    }
    if (t === "eabort") {
      if (!armed("eabort")) { S.arm = { k: "eabort", until: Date.now() + 3000 }; draw(); return setTimeout(draw, 3100); }
      S.arm = null; S.cur = null;
      return eapi("abort", {}).then(function (d) { if (d.__code === 200) S.en = d; draw(); });
    }
    if (t === "edone") { S.en = null; S.cur = null; return eapi("abort", {}).then(function () { enrollPoll(); }); }
    if (t === "gotest") { S.view = "test"; S.last = ""; return eapi("abort", {}).then(function () { S.en = null; draw(); }); }
    if (t === "qsec") { S.quietSec = +v; return draw(); }
    if (t === "tstart") return tStart(v);
    if (t === "tstop") return call("POST", "/assist/test/stop", {}).then(function (d) { if (d.__code === 200) S.test = d; draw(); });
  }
  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest("[data-as]");
    if (!el || el.disabled) return;
    var inBox = $("assistBox") && $("assistBox").contains(el), inSheet = open() && $("sheetBody").contains(el);
    if (!inBox && !inSheet) return;
    e.preventDefault(); e.stopPropagation();
    act(el.getAttribute("data-as"), el);
  });
  document.addEventListener("input", function (e) {
    if (!open() || (e.target.id !== "asName" && e.target.id !== "asHint")) return;
    if (e.target.id === "asName") S.nameIn = e.target.value; else S.hintIn = e.target.value;
    // 只改「用这个名字」能不能点，不重画（重画会打断输入法）
    var st = S.st, b = document.querySelector('[data-as="save"]');
    if (st && b) b.disabled = !!S.busy || !(S.nameIn.trim() !== st.name || S.hintIn.trim() !== (st.hint || ""));
  });
  document.addEventListener("focusout", function (e) {
    if (open() && (e.target.id === "asName" || e.target.id === "asHint")) setTimeout(function () { draw(); }, 0);
  });
  document.addEventListener("keydown", function (e) {
    if (open() && e.key === "Enter" && (e.target.id === "asName" || e.target.id === "asHint")) {
      e.preventDefault();
      var b = document.querySelector('[data-as="save"]'); if (b && !b.disabled) doSave(S.nameIn, S.hintIn);
    }
  });

  window.DeckAssist = { box: box, render: render, leave: leave, show: show };
})();
