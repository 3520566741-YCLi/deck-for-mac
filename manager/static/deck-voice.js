/* Deck 语音层客户端库（只做数据，不画界面）。
 * 连本机语音服务 http://127.0.0.1:8778 的 SSE，断线自动重连。
 *
 *   DeckVoice.on(type, cb)   订阅事件，type 见 manager/voice/INTERFACE.md；"*" 收全部。返回取消函数。
 *   DeckVoice.mic(on)        开/关麦克风
 *   DeckVoice.listen()       不用唤醒词，直接开始听一句
 *   DeckVoice.stop()         停（清播放 + 发 stop 事件，页面据此中止 DeepSeek 请求）
 *   DeckVoice.say(text, done) 把助手回答（增量）交给语音服务：显示 + TTS
 *   DeckVoice.status()       Promise<{state, mic, helper, asr, modules}>
 *   DeckVoice.state          当前状态 idle / listening / thinking / speaking
 *   DeckVoice.micOn          麦克风是否开着
 *   DeckVoice.connected      是否连上语音服务
 */
(function () {
  "use strict";
  var BASE = "http://127.0.0.1:8778";
  var subs = {};
  var es = null;
  var retry = 1000;
  var api = { state: "idle", micOn: false, connected: false, base: BASE };

  function fire(type, ev) {
    (subs[type] || []).concat(subs["*"] || []).forEach(function (cb) {
      try { cb(ev); } catch (e) { console.error("[DeckVoice]", type, e); }
    });
  }

  function connect() {
    try { es = new EventSource(BASE + "/events"); } catch (e) { return later(); }
    es.onopen = function () {
      retry = 1000;
      api.connected = true;
      fire("connection", { type: "connection", connected: true });
    };
    es.onmessage = function (m) {
      var ev;
      try { ev = JSON.parse(m.data); } catch (e) { return; }
      if (ev.type === "state") api.state = ev.state;
      if (ev.type === "stop") lastStopEvent = Date.now();
      if (ev.type === "mic") api.micOn = !!ev.on;
      if (ev.type === "status") { api.state = ev.state; api.micOn = !!ev.mic; }
      fire(ev.type, ev);
    };
    es.onerror = function () {
      if (api.connected) fire("connection", { type: "connection", connected: false });
      api.connected = false;
      es.close();
      later();
    };
  }
  function later() {
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 15000);
  }

  function post(path, body) {
    return fetch(BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }

  api.on = function (type, cb) {
    (subs[type] = subs[type] || []).push(cb);
    return function () { subs[type] = (subs[type] || []).filter(function (f) { return f !== cb; }); };
  };
  api.mic = function (on) { return post("/mic", { on: on !== false }); };
  api.listen = function () { return post("/listen", { source: "ui" }); };
  // 页面收到 stop 事件后会去停 DeepSeek，停 DeepSeek 又会调 DeckVoice.stop()；刚收到过 stop 就不再回发，免得来回循环
  var lastStopEvent = 0;
  api.stop = function () {
    sayBuf = ""; sayDone = false;
    if (Date.now() - lastStopEvent < 1000) return Promise.resolve({ ok: true, skipped: true });
    return post("/stop", { source: "ui" });
  };
  // 助手的增量文字按顺序一段一段送：同时发多个请求可能乱序到达，念出来就颠三倒四。
  // 上一个请求还没回来时，新来的字先攒着，回来后合成一次发出去。
  var sayBuf = "", sayDone = false, sayBusy = false;
  function sayFlush() {
    if (sayBusy || (!sayBuf && !sayDone)) return;
    var body = { text: sayBuf, done: sayDone };
    sayBuf = ""; sayDone = false; sayBusy = true;
    post("/assistant", body).catch(function () {}).then(function () { sayBusy = false; sayFlush(); });
  }
  api.say = function (text, done) {
    sayBuf += text || ""; if (done) sayDone = true;
    sayFlush();
    return Promise.resolve({ ok: true });
  };
  api.status = function () { return fetch(BASE + "/status").then(function (r) { return r.json(); }); };

  window.DeckVoice = api;
  connect();
})();
