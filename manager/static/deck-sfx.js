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
    if (ctx) return;
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

  function play(name) {
    if (!on || muted || document.hidden || NAMES.indexOf(name) < 0) return;
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
  var SKIP = "#micBtn, #ndots, .chat-in, .chat-input, textarea, select, input[type=text], input[type=search], input[type=range], .rz";
  var PICK = "button, a.btn, [data-press], [role=switch], input[type=checkbox], [data-act], [data-nl], #scrim, .card";
  function classify(el, e) {
    if (el.matches("#doneEdit")) return "done";
    if (el.matches("[role=switch]")) return el.getAttribute("aria-checked") === "true" ? "off" : "on";
    if (el.matches("input[type=checkbox]")) return el.checked ? "off" : "on";
    if (el.matches("#closeSheet, #scrim")) return "close";
    if (el.matches("#openSettings, #addCard, [data-act=memory], [data-act=whygo]")) return "open";
    if (el.matches(".seg button, [data-nl], [data-act=tab], [data-act=pkind]")) return el.getAttribute("aria-pressed") === "true" ? "tap" : "tab";
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
    if (e.button > 0 || !e.target.closest) return;
    if (e.target.closest(SKIP)) return;
    var el = e.target.closest(PICK);
    if (!el || el.disabled || el.getAttribute("aria-disabled") === "true") return;
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

  init();   // 先建好（浏览器会让它处于暂停态），音源提前解码；第一次按下时恢复，第一下就有声音

  window.DeckSfx = {
    play: function (n) { init(); play(n); },
    config: function (c) { if (!c) return; if ("token" in c) tok = c.token || ""; if ("on" in c) on = c.on !== false; if (c.vol && VOL[c.vol]) vol = c.vol; level(); },
    mute: function (m) { muted = !!m; },
    get route() { return useIpad() && ipadSeen ? "ipad" : "mac"; }
  };
})();
