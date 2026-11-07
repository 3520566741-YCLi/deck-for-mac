// 用法：DECK_URL=http://127.0.0.1:8777 node scripts/deck-ui-audit.js <截图目录> [soft|glass] [1024x768,800x600] [noshots]
// 需要 playwright。只读：打开监控台的每个页面、弹层、卡片详情、管家页和 Vesper 面板，查文字压边、超出、面和面叠住、文字被截断。
// 自动查「穿模」：文字压在凸起/凹槽的边上、文字超出所在的面、面和面互相叠住、文字被截断
const { chromium } = require('playwright');
const fs = require('fs');
const SURF = '.card,.group,.fu-c,.pai,.mem-item,.seg,.bar,.sheet,.pill,.icon-btn,.ask-pill,.btn,.switch,#whyPop,.banner,.toast,.del-btn,.cmd,.field input,.ring svg,.clean,.chip,.state,.badge,input[type=search],select';
async function audit(p, where) {
  return await p.evaluate(({ SURF, where }) => {
    const out = [];
    const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); if (el.closest('.toast:not(.show),.banner:not(.show),.sheet:not(.show)')) return false;
      // 收起的折叠区里的东西（除了标题行）看不见，但还有尺寸，跳过
      if (el.closest('details:not([open])') && !el.closest('summary')) return false;
      return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth && cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity > 0.05; };
    const name = el => (el.id ? '#' + el.id : el.tagName.toLowerCase() + (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''));
    let scope = document.body;
    const sheet = document.querySelector('.sheet.show'); if (sheet) scope = sheet;
    const pop = document.querySelector('#whyPop.show'); if (pop) scope = pop;
    const clipOf = el => { const sc = el.closest('.card-b,.sheet-b,#whyPop,.card'); return sc ? sc.getBoundingClientRect() : null; };
    const surfaces = [...scope.querySelectorAll(SURF)].filter(vis).filter(el => !el.closest('.chat')).map(el => ({ el, r: el.getBoundingClientRect() }));
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
    const seen = new Set();
    while (walker.nextNode()) {
      const t = walker.currentNode; if (!t.textContent.trim()) continue;
      const el = t.parentElement; if (!el || !vis(el) || el.closest('.chat,script,style,[aria-hidden="true"],.sr-only')) continue;
      const rg = document.createRange(); rg.selectNodeContents(t);
      const cr = clipOf(el);
      // 用省略号截断的文字：Range 量到的是整段原文宽度，按元素自己的框裁一下再查
      const ecs = getComputedStyle(el), clipBox = ecs.textOverflow === 'ellipsis' && ecs.overflowX !== 'visible' ? el.getBoundingClientRect() : null;
      // 自己能滚动的小框（比如 .cmd 里的命令）：滚出框外看不见的那几行不算
      let sb = el; while (sb && sb !== scope) { const o = getComputedStyle(sb).overflowY; if (o === 'auto' || o === 'scroll') break; sb = sb.parentElement; }
      const scrollBox = sb && sb !== scope && sb.scrollHeight > sb.clientHeight + 1 ? sb.getBoundingClientRect() : null;
      for (const r0 of rg.getClientRects()) {
        if (scrollBox && (r0.top >= scrollBox.bottom - 1 || r0.bottom <= scrollBox.top + 1)) continue;
        const r = clipBox ? { left: Math.max(r0.left, clipBox.left), right: Math.min(r0.right, clipBox.right), top: r0.top, bottom: r0.bottom, width: Math.min(r0.right, clipBox.right) - Math.max(r0.left, clipBox.left) } : r0;
        if (r.width < 2) continue;
        // 滚出可视区，或在滚动容器的渐隐区里（顶部 46px、底部 32px 本来就是淡出的）
        const sc0 = el.closest('.card-b,.sheet-b,.chat-log'), fz = sc0 && (sc0.classList.contains('fe-t') || sc0.classList.contains('fe-b'));
        if (cr && (r.bottom > cr.bottom - (fz ? 32 : 2) || r.top < cr.top + (fz ? 46 : 2))) continue;
        for (const s of surfaces) {
          if (s.el.contains(el)) {
            // 贴边：凸起/凹下的块（小方块、分组、卡片）里，文字离左右边不到 6px，看起来就像要穿出去
            if (s.el.matches('.group,.card,.fu-c,.pai,.mem-item,.clean') && r.right <= s.r.right + 1 && r.left >= s.r.left - 1 && (r.left - s.r.left < 6 || s.r.right - r.right < 6)) {
              const k = 't|' + name(s.el) + '|' + t.textContent.trim().slice(0, 20);
              if (!seen.has(k)) { seen.add(k); out.push({ where, kind: '文字离边太近', surf: name(s.el), text: t.textContent.trim().slice(0, 30) }); }
            }
            if (r.left < s.r.left - 1 || r.right > s.r.right + 1 || r.top < s.r.top - 1 || r.bottom > s.r.bottom + 1) {
              const k = 'o|' + name(s.el) + '|' + t.textContent.trim().slice(0, 20);
              if (!seen.has(k)) { seen.add(k); out.push({ where, kind: '文字超出所在的面', surf: name(s.el), text: t.textContent.trim().slice(0, 30) }); }
            }
          } else {
            const ix = Math.min(r.right, s.r.right) - Math.max(r.left, s.r.left), iy = Math.min(r.bottom, s.r.bottom) - Math.max(r.top, s.r.top);
            if (ix > 2 && iy > 2) {
              const scr = clipOf(s.el); if (scr && (Math.max(r.top, s.r.top) > scr.bottom || Math.min(r.bottom, s.r.bottom) < scr.top)) continue;
              // 被叠的面（比如按钮）自己在滚动容器的渐隐区里：那一截本来就是淡出的
              const ssc = s.el.closest('.card-b,.sheet-b,.chat-log'); if (ssc && (ssc.classList.contains('fe-t') || ssc.classList.contains('fe-b'))) { const q = ssc.getBoundingClientRect(); if (s.r.top < q.top + 46 || s.r.bottom > q.bottom - 32) continue; }
              const inside = r.left >= s.r.left && r.right <= s.r.right && r.top >= s.r.top && r.bottom <= s.r.bottom;
              const k = 'x|' + name(s.el) + '|' + t.textContent.trim().slice(0, 20);
              if (!seen.has(k)) { seen.add(k); out.push({ where, kind: inside ? '文字叠在别的面上' : '文字压在面的边上', surf: name(s.el), text: t.textContent.trim().slice(0, 30) }); }
            }
          }
        }
      }
      const cs = getComputedStyle(el);
      if (el.scrollWidth > el.clientWidth + 1 && cs.overflowX !== 'visible' && cs.textOverflow !== 'ellipsis' && cs.whiteSpace === 'nowrap') {
        const k = 'c|' + name(el); if (!seen.has(k)) { seen.add(k); out.push({ where, kind: '文字被截断', surf: name(el), text: el.textContent.trim().slice(0, 30) }); }
      }
    }
    for (let i = 0; i < surfaces.length; i++) for (let j = i + 1; j < surfaces.length; j++) {
      const a = surfaces[i], b = surfaces[j]; if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      if (a.el.closest('.grid-stack-placeholder') || b.el.closest('.grid-stack-placeholder')) continue;
      // 整理模式：卡片内容在「小 宽 高 大」上方已经淡出
      const ix = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left), iy = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
      if (ix > 3 && iy > 3) {
        const sr = clipOf(a.el) || clipOf(b.el); if (sr &&(Math.max(a.r.top, b.r.top) > sr.bottom - 2 || Math.min(a.r.bottom, b.r.bottom) < sr.top + 2)) continue;
        out.push({ where, kind: '两个面叠在一起', surf: name(a.el) + ' × ' + name(b.el), text: (a.el.textContent || '').trim().slice(0, 15) + ' / ' + (b.el.textContent || '').trim().slice(0, 15) });
      }
    }
    return out;
  }, { SURF, where });
}
(async () => {
  const S = process.argv[2], skin = process.argv[3] || 'soft'; fs.mkdirSync(`${S}/audit`, { recursive: true }); const b = await chromium.launch(); const all = []; const errs = [];
  const sizes = (process.argv[4] || '1024x768').split(',').map(x => x.split('x').map(Number));
  const shots = process.argv[5] !== 'noshots';
  for (const [w, h] of sizes) for (const scheme of ['light', 'dark']) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, colorScheme: scheme });
    await ctx.addInitScript(sk => { try { const l = JSON.parse(localStorage.getItem('mbdash.layout') || '{}'); l.skin = sk; localStorage.setItem('mbdash.layout', JSON.stringify(l)); } catch (e) {} }, skin);
    const p = await ctx.newPage(); p.on('pageerror', e => errs.push(e.message));
    const tag = `${skin}-${w}x${h}-${scheme}`;
    const shot = async n => { if (shots) await p.screenshot({ path: `${S}/audit/${tag}-${n}.png` }); };
    await p.goto((process.env.DECK_URL || 'http://127.0.0.1:8777') + '/dash?app=1'); await p.waitForTimeout(2500);
    all.push(...await audit(p, tag + ' 主页')); await shot('main');
    const n = await p.evaluate(() => document.querySelectorAll('.grid-stack-item .card').length);
    for (let i = 0; i < n; i++) {
      const title = await p.evaluate(i => { const c = document.querySelectorAll('.grid-stack-item .card')[i]; c.scrollIntoView({ block: 'center' }); c.click(); const h = c.querySelector('h2'); return h ? h.textContent : String(i); }, i);
      await p.waitForTimeout(800);
      if (await p.evaluate(() => !!document.querySelector('.sheet.show'))) {
        all.push(...await audit(p, `${tag} 详情:${title}`)); await shot('card' + i);
        if (/工具箱/.test(title)) {
          const nt = await p.evaluate(() => document.querySelectorAll('.sheet.show .sheet-b .seg button').length);
          for (let k = 0; k < nt; k++) { await p.evaluate(k => document.querySelectorAll('.sheet.show .sheet-b .seg button')[k].click(), k); await p.waitForTimeout(700); all.push(...await audit(p, `${tag} 工具箱页签${k}`)); await shot('tools' + k); }
        }
        await p.evaluate(() => { const b = document.getElementById('sheetBody'); b.scrollTop = b.scrollHeight; }); await p.waitForTimeout(400);
        all.push(...await audit(p, `${tag} 详情底部:${title}`)); await shot('card' + i + 'b');
        await p.click('#closeSheet'); await p.waitForTimeout(500);
      }
    }
    await p.click('#openSettings'); await p.waitForTimeout(900);
    const H = await p.evaluate(() => { const b = document.getElementById('sheetBody'); return [b.scrollHeight, b.clientHeight]; });
    for (let y = 0, k = 0; y < H[0]; y += H[1] * 0.7, k++) { await p.evaluate(y => document.getElementById('sheetBody').scrollTop = y, y); await p.waitForTimeout(350); all.push(...await audit(p, `${tag} 设置第${k}屏`)); await shot('set' + k); }
    await p.click('#closeSheet'); await p.waitForTimeout(500);
    await p.click('#overall'); await p.waitForTimeout(600); all.push(...await audit(p, `${tag} 注意事项`)); await shot('why');
    await p.click('#overall'); await p.waitForTimeout(500);
    await p.click('#editLayout'); await p.waitForTimeout(900); all.push(...await audit(p, `${tag} 整理模式`)); await shot('edit');
    await p.click('#doneEdit'); await p.waitForTimeout(500);
    // 管家页（iframe）
    await p.click('[data-v="butler"]'); await p.waitForTimeout(1800);
    const fr = p.frames().find(f => /embed=1/.test(f.url()));
    if (fr) { all.push(...await audit(fr, `${tag} 管家页`)); await shot('butler'); await fr.evaluate(() => window.scrollTo(0, 99999)); await p.waitForTimeout(300); all.push(...await audit(fr, `${tag} 管家页底部`)); await shot('butler-b'); }
    await p.click('[data-v="dash"]'); await p.waitForTimeout(800);
    // Vesper（网页面板）：发几条消息，滚到中间，看顶部按钮下面有没有透字
    // 发消息会真的调用 DeepSeek（花钱），所以默认跳过；只在模拟环境里设 AUDIT_CHAT=1
    if (process.env.AUDIT_CHAT === '1') {
    await p.click('#askBtn'); await p.waitForTimeout(700);
    for (const q of ['现在电脑忙不忙？', '打开 GitHub', '今天用了多少']) { await p.fill('#chatText', q); await p.keyboard.press('Enter'); await p.waitForTimeout(1800); }
    await p.evaluate(() => { const l = document.getElementById('chatLog'); l.scrollTop = Math.max(0, l.scrollHeight / 2 - 150); l.dispatchEvent(new Event('scroll')); }); await p.waitForTimeout(500);
    await shot('chat');
    }
    await ctx.close();
  }
  await b.close();
  fs.writeFileSync(`${S}/audit/${skin}.json`, JSON.stringify(all, null, 1));
  const g = {}; for (const f of all) { const k = f.kind + ' | ' + f.surf + ' | ' + f.text; (g[k] = g[k] || []).push(f.where); }
  for (const [k, v] of Object.entries(g)) console.log(v.length + '× ' + k + '   例: ' + v[0]);
  console.log('total', all.length, 'errs', errs.slice(0, 5));
})();
