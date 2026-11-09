// Windows 版界面自查（CI 用）：白天 / 夜里、中文 / 英文，截主页、设置、进程、磁盘面板、清理、管家页、工具箱，
// 并检查 Mac 专属的东西（苹果标志、玻璃、刘海、废纸篓、访达、Dock…）没有漏出来、卡片不重叠、文字不被截断。
// 用法：node windows/ci/shots.js <输出目录>   （先在 127.0.0.1:8777 起好后台）
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const out = process.argv[2] || "shots";
fs.mkdirSync(out, { recursive: true });
const problems = [];
const warnings = [];
// Windows 版界面上不该出现的 Mac 说法（中文 + 英文）
const MAC_WORDS = ["废纸篓", "访达", "程序坞", "LaunchAgent", "这台 Mac", "Spotlight", "钥匙串", "活动监视器", "Trash", "Finder", "Keychain", "Activity Monitor"];
const BASE = "http://127.0.0.1:8777";

async function macWords(page, tag, where) {
  const texts = [await page.evaluate(() => document.body.innerText)];
  for (const f of page.frames()) if (f !== page.mainFrame()) texts.push(await f.evaluate(() => document.body ? document.body.innerText : "").catch(() => ""));
  const all = texts.join("\n");
  for (const w of MAC_WORDS) if (all.includes(w)) problems.push(`[${tag}] ${where}还有 Mac 的说法「${w}」：…${all.slice(Math.max(0, all.indexOf(w) - 20), all.indexOf(w) + 20).replace(/\n/g, " ")}…`);
}

// 文字被截断 / 压边：单行省略号以外，内容比盒子宽（横向溢出）就记下来
async function clipped(page, tag, where, root) {
  const bad = await page.evaluate((sel) => {
    const res = [];
    const r = document.querySelector(sel) || document.body;
    for (const el of r.querySelectorAll("*")) {
      if (!el.offsetParent || !el.childNodes.length) continue;
      const txt = Array.from(el.childNodes).filter((n) => n.nodeType === 3).map((n) => n.nodeValue).join("").trim();
      if (!txt) continue;
      const cs = getComputedStyle(el);
      if (cs.textOverflow === "ellipsis" || /auto|scroll/.test(cs.overflowX)) continue;
      if (el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0 && cs.display !== "inline") res.push(txt.slice(0, 30));
      if (res.length >= 6) break;
    }
    return res;
  }, root);
  if (bad.length) warnings.push(`[${tag}] ${where}可能有文字溢出：${bad.join(" / ")}`);
}

async function shot(page, name) {
  // 云电脑忙的时候渲染一帧会超过默认的 30 秒；动画一律停住（无限循环的动画会让截图一直等新帧）
  await page.screenshot({ path: path.join(out, name + ".png"), animations: "disabled", timeout: 90000 });
}

async function sheet(page, js, wait) {
  await page.evaluate(js);
  await page.waitForTimeout(wait || 1500);
}

async function close(page) {
  await page.evaluate(() => { try { DeckHost.closeSheet(); } catch (e) {} });
  await page.waitForTimeout(500);
}

(async () => {
  const browser = await chromium.launch();
  const runs = [
    { scheme: "light", lang: "zh", size: { width: 1280, height: 800 }, full: true },
    { scheme: "dark", lang: "zh", size: { width: 1280, height: 800 }, full: true },
    { scheme: "light", lang: "en", size: { width: 1280, height: 800 }, full: true },
    { scheme: "dark", lang: "en", size: { width: 1280, height: 800 }, full: false },
    { scheme: "light", lang: "zh", size: { width: 1920, height: 1080 }, full: false },
    { scheme: "dark", lang: "zh", size: { width: 1920, height: 1080 }, full: false },
  ];
  for (const r of runs) {
    const tag = `${r.scheme}-${r.size.width}-${r.lang}`;
    const page = await browser.newPage({ viewport: r.size, deviceScaleFactor: 1, colorScheme: r.scheme });
    page.on("pageerror", (e) => problems.push(`[${tag}] 页面报错：${e.message}`));
    await page.goto(`${BASE}/dash?app=1&lang=${r.lang}`);
    await page.waitForTimeout(6000); // 等系统卡第二次采样（CPU 第一次是空）
    await shot(page, `home-${tag}`);

    const cls = await page.evaluate(() => document.documentElement.className);
    if (!/\bskin-soft\b/.test(cls) || !/\bos-win\b/.test(cls)) problems.push(`[${tag}] 不是新拟态 Windows 版：${cls}`);
    const body = await page.evaluate(() => document.body.innerText);
    if (r.lang === "zh" && !body.includes("电脑实时状态")) problems.push(`[${tag}] 系统卡标题不是「电脑实时状态」`);
    if (/Mac 实时状态|Mac Status/.test(body)) problems.push(`[${tag}] 主页还写着 Mac 实时状态`);
    if (await page.evaluate(() => Array.from(document.querySelectorAll("svg path")).some((e) => (e.getAttribute("d") || "").startsWith("M11.932 6.908")))) problems.push(`[${tag}] 系统卡还是苹果标志`);
    const tabs = await page.evaluate(() => Array.from(document.querySelectorAll("#tabs button")).filter((b) => b.offsetParent).map((b) => b.dataset.v));
    if (!tabs.includes("butler")) problems.push(`[${tag}] 管家页签没出现`);
    for (const k of ["butler", "cleanup"]) if (!(await page.evaluate((k) => !!__deckTest.itemOf(k), k))) problems.push(`[${tag}] 主页没有「${k}」卡`);
    const overlap = await page.evaluate(() => {
      const rs = Array.from(document.querySelectorAll("#grid > .grid-stack-item")).filter((e) => e.offsetParent).map((e) => e.getBoundingClientRect());
      for (let i = 0; i < rs.length; i++) for (let j = i + 1; j < rs.length; j++) {
        const a = rs[i], b = rs[j];
        if (a.left < b.right - 2 && b.left < a.right - 2 && a.top < b.bottom - 2 && b.top < a.bottom - 2) return true;
      }
      return false;
    });
    if (overlap) problems.push(`[${tag}] 卡片重叠`);
    await macWords(page, tag, "主页");
    await clipped(page, tag, "主页", "#grid");

    await page.click("#openSettings");
    await page.waitForTimeout(900);
    await shot(page, `settings-${tag}`);
    const st = await page.evaluate(() => document.getElementById("sheetBody").innerText);
    for (const bad of ["玻璃透明度", "刘海", "皮肤", "Glass", "Notch", "Skin"]) if (st.includes(bad)) problems.push(`[${tag}] 设置里还有「${bad}」`);
    await close(page);

    if (r.full) {
      // 进程：CPU / 内存 / GPU 三页
      for (const k of ["cpu", "mem", "gpu"]) {
        await page.evaluate((k) => __deckTest.openProc(k), k); await page.waitForTimeout(k === "cpu" ? 6000 : 3500);  // 第一次要采两次样
        await shot(page, `proc-${k}-${tag}`);
        const t = await page.evaluate(() => document.getElementById("sheetBody").innerText);
        if (k !== "gpu" && !/\d/.test(t)) problems.push(`[${tag}] 进程页（${k}）没有数据`);
        await macWords(page, tag, `进程页（${k}）`);
        await clipped(page, tag, `进程页（${k}）`, "#sheet");
        await close(page);
      }
      // 磁盘面板：谁在变大 / 最近新增 / 谁在写硬盘
      await sheet(page, () => __deckTest.openDisk(), 4000);
      await shot(page, `disk-grow-${tag}`);
      await macWords(page, tag, "磁盘面板（谁在变大）");
      for (const [t, op] of [["new", "recent"], ["write", "writers"]]) {
        await page.evaluate(([t, op]) => __deckTest.diskTab(t, op), [t, op]); await page.waitForTimeout(5000);
        await shot(page, `disk-${t}-${tag}`);
        await macWords(page, tag, `磁盘面板（${t}）`);
        await clipped(page, tag, `磁盘面板（${t}）`, "#sheet");
      }
      const derr = await page.evaluate(() => document.getElementById("sheetBody").innerText);
      if (/还没做这个功能|not built yet/i.test(derr)) problems.push(`[${tag}] 磁盘面板还是「还没做」`);
      await close(page);
      // 每日清理卡详情、我的应用卡详情
      for (const k of ["cleanup", "butler"]) {
        await page.evaluate((k) => DeckHost.openSheet(k), k); await page.waitForTimeout(1500);
        await shot(page, `card-${k}-${tag}`);
        await macWords(page, tag, `「${k}」卡详情`);
        await clipped(page, tag, `「${k}」卡详情`, "#sheet");
        await close(page);
      }
      // 工具箱（接好后才有这张卡）
      if (await page.evaluate(() => !!__deckTest.itemOf("tools"))) {
        await sheet(page, () => DeckHost.openSheet("tools"), 3000);
        await shot(page, `tools-${tag}`);
        await macWords(page, tag, "工具箱");
        await clipped(page, tag, "工具箱", "#sheet");
        await close(page);
      }
      // 管家页签（嵌着的 index.html）
      await page.click('#tabs button[data-v="butler"]');
      await page.waitForTimeout(5000);
      await shot(page, `butler-${tag}`);
      await macWords(page, tag, "管家页");
      const fr = page.frames().find((f) => f.url().includes("embed=1"));
      if (!fr) problems.push(`[${tag}] 管家页没加载出来`);
      else {
        const t = await fr.evaluate(() => document.body.innerText);
        if (!/Deck/.test(t)) problems.push(`[${tag}] 管家页里没看到 Deck 自己`);
        if (/读取中…|Loading…/.test(t)) problems.push(`[${tag}] 管家页一直在读取`);
      }
    }
    await page.close();
  }
  // 顶部面板（Mac 刘海模式的 Windows 版）：假装有外壳（pywebview.api），看它发给外壳的消息、展开后的样子、面板里的设置
  for (const lang of ["zh", "en"]) {
    const tag = `panel-${lang}`;
    const page = await browser.newPage({ viewport: { width: 920, height: 544 }, deviceScaleFactor: 1 });
    page.on("pageerror", (e) => problems.push(`[${tag}] 页面报错：${e.message}`));
    await page.addInitScript(() => { window.__posts = []; window.pywebview = { api: { post: (n, m) => { window.__posts.push([n, JSON.parse(m)]); } } }; });
    await page.goto(`${BASE}/dash?app=1&notch=1&shell=win&nw=120&nh=36&lang=${lang}`);
    await page.evaluate(() => window.dispatchEvent(new Event("pywebviewready")));
    await page.waitForTimeout(1500);
    const posts = await page.evaluate(() => window.__posts);
    if (!posts.some((p) => p[0] === "deckNotch" && p[1].t === "ready" && p[1].w === 840)) problems.push(`[${tag}] 面板没把 ready / 大小报给外壳：${JSON.stringify(posts)}`);
    if (!posts.some((p) => p[0] === "deckMode" && p[1].t === "get")) problems.push(`[${tag}] 面板没向外壳要显示方式`);
    await page.evaluate(() => { __deckMode({ mode: "notch", active: "notch" }); __notch.set(true); });
    await page.waitForTimeout(2500);
    await shot(page, `panel-open-${lang}`);
    await macWords(page, tag, "顶部面板");
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    if (!(await page.evaluate(() => window.__posts.some((p) => p[0] === "deckNotch" && p[1].t === "close")))) problems.push(`[${tag}] 按 Esc 没让外壳收起面板`);
    await page.evaluate(() => __notch.set(true)); await page.waitForTimeout(1500);
    await page.click("#openSettings"); await page.waitForTimeout(1000);
    await shot(page, `panel-settings-${lang}`);
    const st = await page.evaluate(() => document.getElementById("sheetBody").innerText);
    for (const bad of ["刘海", "扩展屏", "Notch", "External screen", "菜单栏", "menu bar"]) if (st.includes(bad)) problems.push(`[${tag}] 面板设置里还有「${bad}」`);
    if (!st.includes(lang === "zh" ? "顶部面板" : "Top panel")) problems.push(`[${tag}] 面板设置里没有显示方式「顶部面板」`);
    await page.evaluate(() => document.querySelector('[data-act="dmode"][data-v="ext"]').click());
    await page.waitForTimeout(300);
    if (!(await page.evaluate(() => window.__posts.some((p) => p[0] === "deckMode" && p[1].t === "set" && p[1].v === "ext")))) problems.push(`[${tag}] 点「窗口」没告诉外壳`);
    await page.close();
  }
  await browser.close();
  if (warnings.length) console.log("提醒（看一眼截图确认）：\n" + warnings.join("\n"));
  if (problems.length) {
    console.error("界面自查没过：\n" + problems.join("\n"));
    process.exit(1);
  }
  console.log("界面自查通过，截图在 " + out);
})();
