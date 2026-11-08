/* Deck 按键音效（11-07，用户试听六轮后定稿；试听页 https://claude.ai/artifact/4AmyZn4BMJScrYsnWMywQW）
 *
 * 音源：static/sfx/*.wav，取自 snd-lib 1.2.4 的 SND01（代码 MIT，音频免费使用、著作权归声音设计师，见 snd.dev）
 *       和 Kenney Interface Sounds（CC0）；都削掉了 3 kHz 以上的亮声、剪掉开头空白、按「越常用越轻」对齐响度。
 * 规矩：
 *   - 只给「状态变了」出声；悬停、滚动、自动刷新不响。
 *   - 手指一按下就响（pointerdown），不等松手，这样才跟手。
 *   - 同一个声音 40 毫秒内只响一次；快速连点自动变轻、±2% 变调；展开/收起这类 400 毫秒内去重，避免一次操作响两声。
 *   - Vesper 在听或在说时全部静音，免得干扰识别和声纹；助手自己的提示音不归这里管。
 *   - 夜里 23:00–7:00 音量减半；设置 → 外观 → 按键音效 可关、可调音量。
 * 出声位置（）：扩展屏模式从 iPad 出声，刘海模式从 MacBook 出声；Vesper 的听和说一直走 Mac。
 *   OpenDisplay 不传声音，所以 iPad 上装了后台小 App「Deck 音箱」（manager/ipad-speaker/）。扩展屏模式下这里把
 *   {n, g} POST 给本机 /api/sfx，server.py 经数据线（usbmuxd）转给 iPad；回 ok:false（没插 iPad / 没开音箱）
 *   就立刻在 Mac 上放，并且 8 秒内直接在 Mac 上放，之后再试 iPad。
 * 11-08 起这个文件也管「按压手感」（.pressed），并且资讯页、管家页、玻璃对话页都加载它，声音统一交给主页面放。
 * 用法：DeckSfx.play("tap"|"primary"|"tab"|"on"|"off"|"open"|"close"|"page"|"done"|"notify"|"warn")
 *       DeckSfx.config({ on: true, vol: "s"|"m"|"l" })   DeckSfx.mute(true/false)
 */
(function () {
  "use strict";
  var NAMES = ["tap", "primary", "tab", "on", "off", "open", "close", "page", "done", "notify", "warn"];
  var VOL = { s: 0.45, m: 0.75, l: 1.1 };
  var LONG = { open: 1, close: 1, done: 1, notify: 1, page: 1 };   // 这些一次操作只该响一下：400ms 去重
  var ctx = null, out = null, bufs = {}, on = true, vol = "m", muted = false;
  var lastAt = {}, lastAny = 0, voices = {};
  var NOTCH = /[?&]notch=1/.test(location.search);
  // 选了「先从 MacBook 出声」：Mac 上的 Xcode 27 装不了 iOS 15 的 老 iPad，「Deck 音箱」暂时装不上。
  // 先关掉 iPad 通道，免得每 8 秒白问一次服务器、让那一下按键慢半拍。装好 iPad 小音箱后把这里改成 true 即可。
  var IPAD_ON = false;
  var ipadDownUntil = 0, ipadSeen = false;
  var tok = "";                                                  // 本机 Deck 才有（dashboard 的 sfxApply 传进来）；iPad 上用 Safari 看时为空，就在 iPad 本机放
  function token() { return tok; }
  function useIpad() { return IPAD_ON && !NOTCH && !!token() && performance.now() > ipadDownUntil; }
  function sendIpad(name, g, fallback) {
    var x = new XMLHttpRequest();
    x.open("POST", "/api/sfx", true);
    x.setRequestHeader("Content-Type", "application/json"); x.setRequestHeader("X-Dash", "1"); x.setRequestHeader("X-Token", token());
    x.onload = function () { var r = null; try { r = JSON.parse(x.responseText); } catch (e) {} if (r && r.ok) { ipadSeen = true; return; } ipadSeen = false; ipadDownUntil = performance.now() + 8000; fallback(); };
    x.onerror = function () { ipadDownUntil = performance.now() + 8000; fallback(); };
    x.send(JSON.stringify({ n: name, g: g }));
  }

  function init() {
    if (ctx || host()) return;                                    // 嵌在主页面里：声音由主页面放，这里不用再建一份
    var AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
    try { ctx = new AC({ latencyHint: "interactive" }); } catch (e) { try { ctx = new AC(); } catch (e2) { return; } }
    out = ctx.createGain(); out.connect(ctx.destination); level();
    NAMES.forEach(function (n) {
      fetch("/static/sfx/" + n + ".wav").then(function (r) { return r.ok ? r.arrayBuffer() : Promise.reject(r.status); })
        .then(function (ab) { return new Promise(function (ok, bad) { ctx.decodeAudioData(ab, ok, bad); }); })
        .then(function (b) { bufs[n] = b; }, function () {});
    });
  }
  function night() { var h = new Date().getHours(); return h >= 23 || h < 7; }
  function level() { if (out) out.gain.value = (VOL[vol] || VOL.m) * (night() ? 0.5 : 1); }

  // 嵌在监控台里的页面（资讯页、管家页的 iframe）和液态玻璃里的对话页，声音都交给主页面去放：
  // 这样设置里的开关和音量、Vesper 听/说时静音、夜里减半、iPad/Mac 出声位置都只有一份，不会各响各的。
  var forward = null;
  function host() {
    try { if (window.parent !== window && window.parent.DeckSfx && window.parent.DeckSfx !== window.DeckSfx) return window.parent.DeckSfx; } catch (e) {}
    return null;
  }
  function play(name) {
    if (NAMES.indexOf(name) < 0) return;
    var h = host(); if (h) { h.play(name); return; }
    if (forward) { forward(name); return; }
    if (!on || muted || document.hidden) return;
    var now = performance.now();
    if (now - (lastAt[name] || 0) < (LONG[name] ? 400 : 40)) return;
    var g = now - lastAny < 140 ? 0.65 : 1;                       // 快速连点变轻
    lastAt[name] = now; lastAny = now;
    if (useIpad()) {                                              // 扩展屏模式：iPad 出声
      sendIpad(name, g * (VOL[vol] || VOL.m) * (night() ? 0.5 : 1), function () { local(name, g); });
      return;
    }
    local(name, g);
  }
  function local(name, g) {
    if (!ctx) return;
    if (ctx.state === "suspended") { try { ctx.resume(); } catch (e) {} }
    var b = bufs[name]; if (!b) return;
    level();
    var t = ctx.currentTime;
    if (voices[name]) { try { voices[name].g.gain.setTargetAtTime(0, t, 0.008); voices[name].s.stop(t + 0.05); } catch (e) {} }
    var s = ctx.createBufferSource(), vg = ctx.createGain();
    s.buffer = b; s.playbackRate.value = 1 + (Math.random() - 0.5) * 0.04;   // ±2% 变调，连按不像机关枪
    vg.gain.value = g; s.connect(vg); vg.connect(out); s.start(t);
    voices[name] = { s: s, g: vg };
  }

  // ---- 按下就响：按元素类型自动配声音 ----
  // ：「Deck 里所有按钮都要有交互反馈和音效」。所以这里同时管两件事：按下出声 + 按下变样（.pressed）。
  // 任何能点的东西只要落在 PICK 里就自动有声音和按压手感；scripts/deck-press-check.js 会把每个页面、两种皮肤、
  // 扩展屏和刘海模式里所有能点的东西挨个查一遍，漏了就报错（CI 也跑），新按钮没法悄悄漏掉。
  // 要换声音：加 data-sfx="primary" 之类；真不该出声（比如麦克风按钮自己有提示音）：data-sfx="none"，按压手感照样有。
  var SKIP = "textarea, select, input[type=text], input[type=search], input[type=range], input[type=number], .rz, [role=slider]";
  var PICK = "button, a[href], a.btn, summary, label, [data-press], [role=switch], [role=button], [role=tab], [role=menuitem], [role=option], [role=radio], input[type=checkbox], input[type=radio], [data-act], [data-nl], [data-t], [data-sfx], #scrim, .card";
  function pick(t) {
    if (!t || !t.closest) return null;
    if (t.closest(SKIP)) return null;
    var el = t.closest(PICK);
    if (!el || el.disabled || el.getAttribute("aria-disabled") === "true") return null;
    if (el.tagName === "LABEL") { var c = el.control; if (!c || !/^(checkbox|radio)$/.test(c.type) || c.disabled) return pick(el.parentElement); }   // 只有勾选框的说明文字算按钮
    return el;
  }
  function classify(el, e) {
    var forced = el.getAttribute("data-sfx");
    if (forced) return forced === "none" ? null : forced;
    if (el.matches("#doneEdit")) return "done";
    if (el.matches("[role=switch]")) return el.getAttribute("aria-checked") === "true" ? "off" : "on";
    if (el.matches("input[type=checkbox]")) return el.checked ? "off" : "on";
    if (el.tagName === "LABEL") return el.control && el.control.checked ? "off" : "on";
    if (el.matches("#closeSheet, #scrim")) return "close";
    if (el.matches("#openSettings, #addCard, [data-act=memory], [data-act=whygo]")) return "open";
    if (el.matches(".seg button, [data-nl], [data-act=tab], [data-act=pkind], [role=tab], [role=radio]")) return el.getAttribute("aria-pressed") === "true" || el.getAttribute("aria-selected") === "true" || el.getAttribute("aria-checked") === "true" ? "tap" : "tab";
    if (el.matches(".danger, .del-btn, [data-act=remove], [data-act=pkill]")) return "warn";
    if (el.matches(".primary")) return "primary";
    if (el.matches(".card")) {
      // 点卡片打开详情：鼠标按下就响；手指可能是在滑动，等 openSheet 真打开时再响
      if (document.body.classList.contains("editing")) return null;   // 整理模式：拿起时由 dragstart 响
      return e.pointerType === "mouse" ? "open" : null;
    }
    return "tap";
  }
  document.addEventListener("pointerdown", function (e) {
    init();
    if (e.button > 0) return;
    var el = pick(e.target); if (!el) return;
    var n = classify(el, e); if (n) play(n);
  }, true);
  // 键盘：回车 / 空格按按钮也给声音
  document.addEventListener("keydown", function (e) {
    if ((e.key !== "Enter" && e.key !== " ") || e.repeat) return;
    var el = document.activeElement; if (!el || !el.matches || !el.matches(PICK) || el.closest(SKIP)) return;
    var n = classify(el, { pointerType: "keyboard" }); if (n) play(n);
  }, true);
  document.addEventListener("visibilitychange", function () {
    if (!ctx) return;
    if (document.hidden) { try { ctx.suspend(); } catch (e) {} } else { try { ctx.resume(); } catch (e) {} }
  });

  // ---- 按压手感：鼠标一按就下沉；手指 0.1 秒后才下沉（免得滑动时乱闪），但快速点一下也会「闪」一下，不会毫无反应 ----
  // 每个页面自己写 .pressed 长什么样（新拟态是凹下去、玻璃是缩一点）；没写的统一用下面这条兜底（:where 不加权重，页面的规则总是赢）。
  (function () {
    var st = document.createElement("style");
    st.textContent = ":where(.pressed){transform:scale(.96);transition:transform .12s cubic-bezier(.2,.8,.2,1),opacity .12s,box-shadow .1s}" +
      ":where(.pressed.touch){opacity:.85}" +
      "@media (prefers-reduced-motion: reduce){:where(.pressed){transform:none;opacity:.7}}" +
      ":where(:root.reduce .pressed){transform:none;opacity:.7}";
    (document.head || document.documentElement).appendChild(st);
    var cur = null, timer = null, flash = null, sx = 0, sy = 0, shown = false;
    function clear() { clearTimeout(timer); if (cur) cur.classList.remove("pressed", "touch"); cur = null; shown = false; }
    function show(el, touch) { el.classList.add("pressed"); if (touch) el.classList.add("touch"); shown = true; }
    document.addEventListener("pointerdown", function (e) {
      if (e.button > 0) return;
      var el = pick(e.target);
      clear(); if (!el) return;
      if (document.body && document.body.classList.contains("editing") && el.matches(".card")) return;   // 整理模式拖卡片不下沉
      cur = el; sx = e.clientX; sy = e.clientY;
      if (e.pointerType === "mouse" || e.pointerType === "pen") show(el, false);
      else timer = setTimeout(function () { if (cur === el) show(el, true); }, 100);
    }, { passive: true, capture: true });
    document.addEventListener("pointermove", function (e) { if (cur && (Math.abs(e.clientX - sx) > 10 || Math.abs(e.clientY - sy) > 10)) clear(); }, { passive: true });
    document.addEventListener("pointerup", function () {
      var el = cur, was = shown; clearTimeout(timer); cur = null; shown = false;
      if (!el) return;
      if (was) { el.classList.remove("pressed", "touch"); return; }
      // 手指很快点一下（不到 0.1 秒）：补一下按下的样子，再弹回来
      el.classList.add("pressed", "touch"); clearTimeout(flash);
      flash = setTimeout(function () { el.classList.remove("pressed", "touch"); }, 140);
    }, { passive: true, capture: true });
    ["pointercancel", "pointerleave", "blur"].forEach(function (ev) { (ev === "blur" ? window : document).addEventListener(ev, clear, { passive: true }); });
  })();

  init();   // 先建好（浏览器会让它处于暂停态），音源提前解码；第一次按下时恢复，第一下就有声音

  window.DeckSfx = {
    play: function (n) { init(); play(n); },
    config: function (c) { if (!c) return; if ("forward" in c) forward = typeof c.forward === "function" ? c.forward : null; if ("token" in c) tok = c.token || ""; if ("on" in c) on = c.on !== false; if (c.vol && VOL[c.vol]) vol = c.vol; level(); },
    mute: function (m) { muted = !!m; },
    // 给 scripts/deck-press-check.js 用：这个元素按下时归谁、出什么声
    pick: pick,
    soundFor: function (el) { var p = pick(el); return p ? classify(p, { pointerType: "mouse" }) : null; },
    get route() { return useIpad() && ipadSeen ? "ipad" : "mac"; }
  };
})();
