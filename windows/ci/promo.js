// Windows 版产品图（CI 用，公开仓库 README 和 Release 用）：中文 / 英文各一套，2 倍清晰度。
// 在 GitHub 的 Windows 云电脑上真跑出来的画面；云电脑上没有 Claude Code / Codex / DeepSeek / GitHub 账号，
// 这四张卡在设置里关掉（用户自己也能这么关），其余都是真实数据。
// 用法：node windows/ci/promo.js <输出目录>   （先在 127.0.0.1:8777 起好后台）
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const out = process.argv[2] || "promo";
fs.mkdirSync(out, { recursive: true });
const BASE = "http://127.0.0.1:8777";
const HIDE = ["claude", "codex", "deepseek", "github"];
const problems = [];

const SPECS = [
  { name: "win-home-day", h: 640 },
  { name: "win-home-night", dark: true, h: 640 },
  { name: "win-panel", url: "/dash?app=1&notch=1&shell=win&nw=120&nh=36", w: 840, h: 480, panel: true },
  { name: "win-toolbox", acts: [() => DeckHost.openSheet("__tools"), 1500], until: () => !/正在列出应用|Listing apps/.test(document.querySelector("#sheet").innerText), el: "#sheet", w: 1024, h: 760 },
  { name: "win-process", dark: true, acts: [() => __deckTest.openProc("cpu"), 7000], el: "#sheet", w: 1024, h: 760 },
  { name: "win-disk", acts: [() => __deckTest.openDisk(), 6000], el: "#sheet", w: 1024, h: 760 },
  { name: "win-settings", acts: [() => DeckHost.openSheet("__settings"), 1500], el: "#sheet", w: 1024, h: 760 },
  { name: "win-butler", acts: [() => document.querySelector('#tabs button[data-v="butler"]').click(), 6000] },
];

(async () => {
  const browser = await chromium.launch();
  for (const lang of ["zh", "en"]) {
    for (const s of SPECS) {
      // 云电脑偶尔整页卡住（主分支上也出现过 goto / screenshot 30 秒超时）：同一张图最多重拍 3 次，别让偶发卡顿判失败
      for (let tries = 1; ; tries++) {
        try { await shoot(browser, s, lang); break; }
        catch (e) {
          if (tries >= 3) throw e;
          console.log(`[${s.name}-${lang}] 第 ${tries} 次卡住（${e.name}），重拍`);
        }
      }
    }
  }
  await browser.close();
  if (problems.length) { console.error(problems.join("\n")); process.exit(1); }
  console.log("产品图拍完：" + out);
})();

async function shoot(browser, s, lang) {
  const mine = []; // 这一次拍的问题；重拍时丢掉上一次的
  const ctx = await browser.newContext({ viewport: { width: s.w || 1280, height: s.h || 800 }, deviceScaleFactor: 2, colorScheme: s.dark ? "dark" : "light" });
  try {
    await ctx.addInitScript(([hide, lang, panel]) => {
      try {
        const l = JSON.parse(localStorage.getItem("mbdash.layout") || "{}");
        Object.assign(l, { v: 3, hidden: hide });
        localStorage.setItem("mbdash.layout", JSON.stringify(l));
        localStorage.setItem("deck.lang", lang);
      } catch (e) {}
      if (panel) window.pywebview = { api: { post: () => {} } };
    }, [HIDE, lang, !!s.panel]);
    const page = await ctx.newPage();
    page.on("pageerror", (e) => mine.push(`[${s.name}-${lang}] 页面报错：${e.message}`));
    await page.goto(`${BASE}${s.url || "/dash?app=1"}${(s.url || "").includes("?") || !s.url ? "&" : "?"}lang=${lang}`);
    if (s.panel) {
      await page.evaluate(() => window.dispatchEvent(new Event("pywebviewready")));
      await page.waitForTimeout(1200);
      await page.evaluate(() => { __deckMode({ mode: "notch", active: "notch" }); __notch.set(true); });
    }
    await page.waitForTimeout(7000); // 系统卡第二次采样、我的应用读完
    for (const a of s.acts || []) {
      if (typeof a === "number") await page.waitForTimeout(a);
      else await page.evaluate(a).catch((e) => mine.push(`[${s.name}-${lang}] ${e.message}`));
    }
    // 列表要等真读完（云电脑第一次列应用要十几秒，机器忙时会超过一分钟），别拍到「正在列出…」
    if (s.until) await page.waitForFunction(s.until, null, { timeout: 120000, polling: 500 }).then(() => page.waitForTimeout(800)).catch(() => mine.push(`[${s.name}-${lang}] 等了 120 秒还没读完`));
    const file = path.join(out, `${s.name}-${lang}.png`);
    // 弹层里的列表每几秒刷新一次，locator.screenshot 等「元素稳定」会一直等不到：直接按弹层的位置裁
    const clip = s.el ? await page.locator(s.el).boundingBox() : null;
    await page.screenshot({ path: file, animations: "disabled", ...(clip ? { clip } : {}) });
    if (lang === "en") {
      const miss = await page.evaluate(() => (window.DeckLang && DeckLang.missing ? DeckLang.missing() : []));
      if (miss.length) mine.push(`[${s.name}-en] 还有中文：${miss.slice(0, 8).join(" | ")}`);
    }
    problems.push(...mine);
  } finally {
    await ctx.close().catch(() => {});
  }
}
