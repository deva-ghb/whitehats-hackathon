// What every WhiteHats demo recorder shares: each person's screen captured through the Chrome screencast with a drawn
// cursor, the moves a person makes on camera, and the story beats logged with their times for demo-video/build_video.py.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { BASE, totp } from './ledgerly.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
export const OUT = path.resolve(here, '../../../demo-video', process.env.FOOTAGE || 'footage')
export const REHEARSE = process.env.REHEARSE === '1'
export const VIEW = { width: 1440, height: 810 }
export const SCALE = 2
export const ARGS = ['--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding', '--disable-background-timer-throttling']
const SECRETS = path.join(os.homedir(), '.whitehats-demo')
export const person = (who) => JSON.parse(fs.readFileSync(path.join(SECRETS, `ledgerly-${who}.json`), 'utf8'))

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
export const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a)

// ------------------------------------------------------------------ drawn cursor
// Screencast frames carry no OS cursor, so the page draws one that follows the mouse and ripples on click.
// A person's real mouse moves are drawn too, so the one click a person makes on GitHub shows on camera.
export const CURSOR = () => {
  const mount = () => {
    if (document.getElementById('__demo_cursor')) return
    const s = document.createElement('style')
    s.textContent = `
      #__demo_cursor{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;pointer-events:none;
        transform:translate(-100px,-100px);transition:none;filter:drop-shadow(0 2px 3px rgba(0,0,0,.35))}
      .__demo_ripple{position:fixed;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;z-index:2147483646;
        pointer-events:none;border:3px solid #0F766E;background:rgba(20,184,166,.2);animation:__demo_r .55s ease-out forwards}
      @keyframes __demo_r{from{transform:scale(.3);opacity:1}to{transform:scale(1.25);opacity:0}}`
    document.documentElement.appendChild(s)
    const c = document.createElement('div')
    c.id = '__demo_cursor'
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 26 26"><path d="M4 2 L4 21 L9 16.5 L12.5 24 L15.6 22.6 L12.2 15.3 L19 15.3 Z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>'
    document.documentElement.appendChild(c)
    const p = window.__demo_pos || { x: innerWidth / 2, y: innerHeight / 2 }
    c.style.transform = `translate(${p.x - 4}px,${p.y - 2}px)`
  }
  const move = (e) => {
    window.__demo_pos = { x: e.clientX, y: e.clientY }
    const c = document.getElementById('__demo_cursor')
    if (c) c.style.transform = `translate(${e.clientX - 4}px,${e.clientY - 2}px)`
  }
  addEventListener('mousemove', move, true)
  addEventListener('mousedown', (e) => {
    const r = document.createElement('div')
    r.className = '__demo_ripple'
    r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px'
    document.documentElement.appendChild(r)
    setTimeout(() => r.remove(), 600)
  }, true)
  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', mount)
  else mount()
  new MutationObserver(mount).observe(document, { childList: true, subtree: true })
}

// ------------------------------------------------------------------ one screen = one page
export class Screen {
  constructor(name, ctx, page) { Object.assign(this, { name, ctx, page, frames: [], track: [], pos: { x: 720, y: 405 }, n: 0 }) }

  static async open(ctx, name) {
    const page = await ctx.newPage()
    const s = new Screen(name, ctx, page)
    page.on('pageerror', (e) => log(`[${name}] page error:`, String(e)))
    await s.startCapture()
    return s
  }

  async startCapture() {
    this.dir = path.join(OUT, this.name)
    fs.mkdirSync(this.dir, { recursive: true })
    this.cdp = await this.ctx.newCDPSession(this.page)
    this.writes = []
    this.cdp.on('Page.screencastFrame', ({ data, sessionId }) => {
      this.cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {})
      const file = `${String(this.n++).padStart(6, '0')}.jpg`
      this.frames.push([Date.now(), file])
      this.writes.push(fs.promises.writeFile(path.join(this.dir, file), Buffer.from(data, 'base64')))
    })
  }

  async on() {
    if (REHEARSE) return
    await this.page.bringToFront()
    await this.cdp.send('Page.startScreencast', { format: 'jpeg', quality: 90, maxWidth: 2880, maxHeight: 1620, everyNthFrame: 1 })
    const n = this.n
    for (let i = 0; i < 40 && this.n === n; i++) await sleep(25)
  }

  async off() { if (!REHEARSE) { try { await this.cdp.send('Page.stopScreencast') } catch { /* closed */ } } }

  async stopCapture() {
    await this.off()
    await Promise.all(this.writes)
    fs.writeFileSync(path.join(this.dir, 'frames.json'), JSON.stringify(this.frames))
    fs.writeFileSync(path.join(this.dir, 'track.json'), JSON.stringify(this.track))
  }

  async glide(x, y, ms) {
    const { x: x0, y: y0 } = this.pos
    const dist = Math.hypot(x - x0, y - y0)
    const dur = ms ?? Math.min(900, Math.max(350, dist * 0.9))
    const steps = Math.max(8, Math.round(dur / 16))
    for (let i = 1; i <= steps; i++) {
      const t = i / steps, e = t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2
      await this.page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
      await sleep(dur / steps)
    }
    this.pos = { x, y }
    this.track.push([Date.now(), Math.round(x), Math.round(y)])
  }

  async point(loc, { dx = 0.5, dy = 0.5 } = {}) {
    await loc.waitFor({ state: 'visible', timeout: 30_000 })
    await this.reveal(loc)
    const b = await loc.boundingBox()
    await this.glide(b.x + b.width * dx, b.y + b.height * dy)
  }

  async click(loc, { after = 450 } = {}) {
    await this.point(loc)
    await sleep(180)
    await this.page.mouse.down(); await sleep(70); await this.page.mouse.up()
    await sleep(after)
  }

  async type(loc, text, { delay = 38 } = {}) {
    await this.click(loc, { after: 150 })
    await loc.fill('')
    await loc.pressSequentially(text, { delay })
    await sleep(300)
  }

  // The web app's dropdowns are Radix selects: open the trigger, then click the option.
  async select(loc, label) {
    await this.click(loc, { after: 500 })
    await this.click(this.page.getByRole('option', { name: label, exact: true }), { after: 500 })
  }

  async reveal(loc) {
    const moved = await loc.evaluate((el) => {
      const r = el.getBoundingClientRect()
      if (r.top > 70 && r.bottom < innerHeight - 40) return false
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return true
    })
    if (moved) await sleep(800)
  }

  async scrollBy(dy, ms = 1100) { await this.page.evaluate(([d]) => scrollBy({ top: d, behavior: 'smooth' }), [dy]); await sleep(ms) }
  async scrollTop() { await this.page.evaluate(() => scrollTo({ top: 0, behavior: 'smooth' })); await sleep(700) }

  async go(url, wait) {
    await this.page.goto(url.startsWith('http') ? url : BASE + url, { waitUntil: 'domcontentloaded' })
    if (wait) await wait.waitFor({ timeout: 30_000 })
    await sleep(900)
  }

  // Signs in through the sign-in screen with the person's two-step code. Slow on camera, quick off it.
  async signIn(who, { slow = true } = {}) {
    const me = person(who)
    const p = this.page
    await this.go('/')
    if (await p.getByRole('button', { name: 'Sign out' }).isVisible().catch(() => false)) {
      await p.getByRole('button', { name: 'Sign out' }).click(); await sleep(800)
    }
    await p.locator('#email').waitFor()
    const fill = slow ? (loc, text) => this.type(loc, text, { delay: 45 }) : (loc, text) => loc.fill(text)
    await fill(p.locator('#email'), me.email)
    await fill(p.locator('#password'), me.password)
    slow ? await this.click(p.getByRole('button', { name: 'Sign in' })) : await p.getByRole('button', { name: 'Sign in' }).click()
    // Two-step sign-in, when the person has it switched on: the code from their authenticator.
    const code = p.getByLabel('Code')
    const asked = await Promise.race([code.waitFor({ timeout: 8000 }).then(() => true, () => false),
      p.getByRole('button', { name: 'Sign out' }).waitFor({ timeout: 8000 }).then(() => false, () => false)])
    if (asked) {
      await fill(code, totp(me.totp))
      slow ? await this.click(p.getByRole('button', { name: 'Continue' })) : await p.getByRole('button', { name: 'Continue' }).click()
    }
    const agree = p.getByRole('button', { name: 'I agree' })
    if (await agree.waitFor({ timeout: 4000 }).then(() => true, () => false)) await this.click(agree)
    await p.getByRole('button', { name: 'Sign out' }).waitFor({ timeout: 30_000 })
    await sleep(1000)
  }

  // A person signs in themselves, off camera, in the recorder's own window; the take goes on once they are in.
  async awaitSignIn(name, { limit = 15 * 60_000 } = {}) {
    await this.go('/')
    log(`waiting for ${name} to sign in in the recorder window`)
    await this.page.getByRole('button', { name: 'Sign out' }).waitFor({ timeout: limit })
    log(`${name} is signed in`)
    await sleep(1000)
  }

  // The API, with this person's session, for waits and facts the story needs (never to do what the camera shows).
  api(method, url, body) {
    return this.page.evaluate(async ([m, u, b]) => {
      const r = await fetch('/api' + u, { method: m, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('wh.token') }, body: b ? JSON.stringify(b) : undefined })
      if (!r.ok) throw new Error(`${m} ${u} -> ${r.status} ${await r.text()}`)
      return r.json()
    }, [method, url, body])
  }
}

// ------------------------------------------------------------------ beats
export const beats = []
export async function beat(id, who, fn, extra = {}) {
  await who.on()
  const t0 = Date.now()
  log(`beat ${id} (${who.name})`)
  try { await fn() } finally {
    beats.push({ id, who: who.name, t0, t1: Date.now(), ...extra })
    await who.off()
  }
}
export const wait = (id, who, fn) => beat(id, who, fn, { wait: true })

// Polls until `test` returns something truthy, reloading the page between tries so the screen shows the change.
export async function until(who, what, test, { every = 15_000, limit = 30 * 60_000, reload = true } = {}) {
  const end = Date.now() + limit
  for (;;) {
    const found = await test().catch((e) => { if (e.fatal) throw e; log(`  ${what}: ${e.message.slice(0, 120)}`); return null })
    if (found) return found
    if (Date.now() > end) throw new Error(`gave up waiting for ${what}`)
    await sleep(every)
    if (reload) { await who.page.reload({ waitUntil: 'domcontentloaded' }); await sleep(1500) }
  }
}

