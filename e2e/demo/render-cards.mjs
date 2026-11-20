// Adapted from the Mediar demo (sos/working-prototype/e2e/demo). Renders the video's title cards, chapter cards and on-screen labels to PNG at 1920x1080.
//   node demo/render-cards.mjs <cards.json> <out-dir>
// cards.json: [{ "kind": "title"|"chapter"|"outro"|"person"|"caption", "out": "name.png", ...fields }]
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const [specFile, outDir] = process.argv.slice(2)
const cards = JSON.parse(fs.readFileSync(specFile, 'utf8'))
fs.mkdirSync(outDir, { recursive: true })

const esc = (s = '') => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;')

const BASE = `
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root{--flame:#14B8A6;--flame-dark:#0F766E;--navy:#0F1A2A;--slate:#41556E;--slate-light:#7A8CA3;--bg:#F4F6F8;--teal:#0F766E;--green:#14B8A6}
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{width:1920px;height:1080px;font-family:'Manrope',sans-serif;color:var(--navy);-webkit-font-smoothing:antialiased}
  .stage{position:relative;width:1920px;height:1080px;overflow:hidden;background:var(--bg);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}
  .glow{position:absolute;border-radius:50%;filter:blur(90px);opacity:.55}
  .g1{width:760px;height:760px;left:-180px;top:-260px;background:#BFE8E2}
  .g2{width:680px;height:680px;right:-160px;bottom:-280px;background:#D3DEEA}
  .g3{width:520px;height:520px;right:340px;top:-300px;background:#C9F0E8;opacity:.45}
  .brand{display:flex;align-items:center;gap:26px;position:relative}
  .dot{width:112px;height:112px;border-radius:28px;background:linear-gradient(135deg,#14B8A6,#0F766E);color:#fff;font-weight:800;font-size:64px;display:flex;align-items:center;justify-content:center;box-shadow:0 14px 40px rgba(15,118,110,.35)}
  .name{font-size:132px;font-weight:600;letter-spacing:-4px;line-height:1}
  .kicker{position:relative;margin-top:44px;font-size:26px;font-weight:500;letter-spacing:5px;text-transform:uppercase;color:var(--flame-dark)}
  .sub{position:relative;margin-top:22px;font-size:40px;font-weight:300;color:var(--slate)}
  .rule{position:relative;width:140px;height:6px;border-radius:3px;margin-top:40px;background:linear-gradient(90deg,#0F766E,#14B8A6,#5EEAD4,#41556E,#0F1A2A)}
  .num{position:relative;width:150px;height:150px;border-radius:50%;background:linear-gradient(135deg,#14B8A6,#0F766E);color:#fff;font-size:76px;font-weight:600;display:flex;align-items:center;justify-content:center;box-shadow:0 16px 44px rgba(15,118,110,.32)}
  .chap{position:relative;margin-top:44px;font-size:24px;letter-spacing:6px;text-transform:uppercase;color:var(--slate-light);font-weight:500}
  .ctitle{position:relative;margin-top:16px;font-size:84px;font-weight:600;letter-spacing:-2px}
  .clear{background:transparent!important}
  .pill{position:absolute;left:48px;top:968px;display:flex;align-items:center;gap:16px;padding:16px 30px 16px 20px;border-radius:999px;background:rgba(255,255,255,.96);box-shadow:0 8px 30px rgba(33,71,91,.22);font-size:28px;font-weight:500}
  .av{width:44px;height:44px;border-radius:50%;color:#fff;font-weight:600;font-size:21px;display:flex;align-items:center;justify-content:center}
  .role{color:var(--slate);font-weight:400}
  .cap.top{bottom:auto;top:96px}
  .cap{position:absolute;left:50%;transform:translateX(-50%);bottom:132px;max-width:1500px;padding:18px 34px;border-radius:18px;background:rgba(23,35,48,.84);color:#fff;font-size:32px;line-height:1.38;font-weight:400;text-align:center;text-wrap:balance;box-shadow:0 10px 34px rgba(0,0,0,.25)}
</style>`

function html(c) {
  if (c.kind === 'title' || c.kind === 'outro') return `<div class="stage"><div class="glow g1"></div><div class="glow g2"></div><div class="glow g3"></div>
    <div class="brand"><div class="dot">W</div><div class="name">${esc(c.title)}</div></div>
    <div class="kicker">${esc(c.kicker)}</div>${c.sub ? `<div class="sub">${esc(c.sub)}</div>` : ''}<div class="rule"></div></div>`
  if (c.kind === 'chapter') return `<div class="stage"><div class="glow g1"></div><div class="glow g2"></div>
    <div class="num">${esc(c.n)}</div><div class="chap">Chapter ${esc(c.n)} of ${esc(c.of)}</div><div class="ctitle">${esc(c.title)}</div><div class="rule"></div></div>`
  if (c.kind === 'person') {
    const [who, role] = c.label.split(' · ')
    const colour = c.role === 'agent' ? 'linear-gradient(135deg,#14B8A6,#0F766E)' : 'linear-gradient(135deg,#41556E,#0F1A2A)'
    return `<div class="stage clear"><div class="pill"><div class="av" style="background:${colour}">${esc(who[0])}</div>
      <span>${esc(who)}</span><span class="role">· ${esc(role)}</span></div></div>`
  }
  if (c.kind === 'caption') return `<div class="stage clear"><div class="cap ${c.side === 'top' ? 'top' : ''}">${esc(c.text)}</div></div>`
  throw new Error('unknown card ' + c.kind)
}

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } })
for (const c of cards) {
  await page.setContent(`<!doctype html><html><head><meta charset="utf-8">${BASE}</head><body>${html(c)}</body></html>`, { waitUntil: 'networkidle' })
  await page.evaluate(() => document.fonts.ready)
  await page.screenshot({ path: path.join(outDir, c.out), omitBackground: ['person', 'caption'].includes(c.kind) })
}
await browser.close()
console.log(`rendered ${cards.length} cards into ${outDir}`)
