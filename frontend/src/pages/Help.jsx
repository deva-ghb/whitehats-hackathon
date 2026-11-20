import { useEffect, useMemo, useRef } from 'react'
import { Marked } from 'marked'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useApi } from '../lib/api'
import { Card, ErrorNote, Loading } from '../components/ui'

const SECTIONS = { start: 'Start here', operations: 'Operations', deploy: 'Deploying', connectors: 'Connectors', developers: 'Developers' }

const SLUG_CHARS = 'abcdefghijklmnopqrstuvwxyz0123456789 -'
function slugify(text) {
  return text.toLowerCase().trim().split('').filter((c) => SLUG_CHARS.includes(c)).join('').split(' ').filter(Boolean).join('-')
}

// Docs link to each other as relative .md files; inside the app they become /help/... routes.
function resolveLink(href, slug) {
  if (!href || href.startsWith('http') || href.startsWith('#') || !href.includes('.md')) return href
  const [file, hash] = href.split('#')
  const parts = slug.split('/').slice(0, -1)
  for (const seg of file.replace('.md', '').split('/')) {
    if (seg === '..') parts.pop()
    else if (seg !== '.') parts.push(seg)
  }
  return `/help/${parts.join('/')}${hash ? `#${hash}` : ''}`
}

function render(markdown, slug) {
  const md = new Marked()
  md.use({
    renderer: {
      heading({ tokens, depth, text }) {
        const raw = this.parser.parseInline(tokens)
        const marker = raw.lastIndexOf('{#')
        const id = marker >= 0 && raw.endsWith('}') ? raw.slice(marker + 2, -1) : slugify(text)
        const label = marker >= 0 && raw.endsWith('}') ? raw.slice(0, marker).trim() : raw
        return `<h${depth} id="${id}">${label}</h${depth}>`
      },
      link({ href, title, tokens }) {
        const to = resolveLink(href, slug)
        const external = to?.startsWith('http')
        return `<a href="${to}"${title ? ` title="${title}"` : ''}${external ? ' target="_blank" rel="noreferrer"' : ''}>${this.parser.parseInline(tokens)}</a>`
      },
    },
  })
  return md.parse(markdown)
}

export default function Help() {
  const loc = useLocation()
  const nav = useNavigate()
  let rest = loc.pathname.slice('/help'.length)
  if (rest.startsWith('/')) rest = rest.slice(1)
  const slug = decodeURIComponent(rest) || 'index'
  const pages = useApi('/api/help')
  const page = useApi(`/api/help/${slug}`, { deps: [slug] })
  const body = useRef(null)
  const html = useMemo(() => (page.data ? render(page.data.markdown, slug) : ''), [page.data, slug])
  useEffect(() => {
    if (!html) return
    const id = loc.hash.slice(1)
    const el = id && document.getElementById(id)
    if (el) el.scrollIntoView({ block: 'start' })
    else window.scrollTo(0, 0)
  }, [html, loc.hash])
  const onClick = (e) => {
    const a = e.target.closest('a')
    if (a && a.getAttribute('href')?.startsWith('/')) { e.preventDefault(); nav(a.getAttribute('href')) }
  }
  const groups = (pages.data || []).reduce((g, p) => ({ ...g, [p.section]: [...(g[p.section] || []), p] }), {})
  return (
    <div className="help">
      <aside className="help-nav" aria-label="Help pages">
        {Object.keys(SECTIONS).filter((s) => groups[s]).map((s) => (
          <div key={s}><div className="nav-label">{SECTIONS[s]}</div>
            {groups[s].map((p) => <NavLink key={p.slug} to={`/help/${p.slug === 'index' ? '' : p.slug}`} end>{p.title}</NavLink>)}</div>
        ))}
      </aside>
      <Card>
        {page.error ? <div className="card-body"><ErrorNote error={page.error} /></div> : !page.data ? <Loading rows={8} /> :
          <article className="doc card-body" ref={body} onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />}
      </Card>
    </div>
  )
}
