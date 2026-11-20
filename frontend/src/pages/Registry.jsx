// The WhiteHats Registry (REG.1): a catalogue of fixed builds of libraries whose upstream release still carries a
// flaw, with the proof each one passed, and the tokens that let a build system install them. Images come later.
import { useMemo, useState } from 'react'
import { api, fmt, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Card, CodeBlock, Empty, ErrorNote, Field, Icon, Loading, Modal, Tabs, useAction } from '../components/ui'

const indexUrl = () => `${window.location.origin}/api/registry/simple/`

/** The language each package index serves, in the order the filter lists them. */
const LANGUAGES = { PyPI: 'Python', npm: 'JavaScript', Maven: 'Java', Go: 'Go', NuGet: '.NET' }
const language = (ecosystem) => LANGUAGES[ecosystem] || ecosystem

const STATES = [['all', 'Any status'], ['ready', 'Ready to install'], ['waiting', 'Waiting for a build']]

/** One catalogue entry per library: its fixed builds, newest first, and a recipe still waiting to be built. */
function catalogue(data) {
  const byKey = new Map()
  const entry = (ecosystem, name) => {
    const key = `${ecosystem}:${name.toLowerCase()}`
    if (!byKey.has(key)) byKey.set(key, { key, ecosystem, name, builds: [], waiting: [] })
    return byKey.get(key)
  }
  for (const p of data.packages) entry(p.ecosystem, p.name).builds.push(p)
  const built = new Set(data.packages.map((p) => `${p.name} ${p.upstream_version}`))
  for (const r of data.recipes) if (!built.has(`${r.name} ${r.upstream_version}`)) entry(r.ecosystem, r.name).waiting.push(r)
  return [...byKey.values()].map((lib) => {
    const latest = lib.builds.find((b) => b.status === 'proved') || lib.builds[0] || null
    const advisories = [...new Set([...lib.builds, ...lib.waiting].flatMap((x) => x.advisories))]
    const summary = (latest || lib.waiting[0])?.summary || ''
    const state = lib.builds.some((b) => b.status === 'proved') ? 'ready' : lib.waiting.length ? 'waiting' : 'failed'
    return { ...lib, latest, advisories, summary, state, language: language(lib.ecosystem) }
  }).sort((a, b) => a.name.localeCompare(b.name))
}

export default function Registry() {
  const { user } = useAuth()
  const { data, error, loading, reload } = useApi('/api/registry')
  const [tab, setTab] = useState('libraries')
  const [making, setMaking] = useState(false)
  const libraries = useMemo(() => (data ? catalogue(data) : []), [data])
  if (loading && !data) return <Loading rows={4} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const manage = allowed(user, 'manage_connections')
  const live = data.tokens.filter((t) => !t.revoked && !t.expired).length
  return (
    <div>
      <div className="page-head">
        <div>
          <h2>WhiteHats Registry</h2>
          <p>Your company's catalogue of fixed software. When something your code uses has a known flaw and no fixed
            release, WhiteHats builds a fixed version from the upstream source, proves the flaw is gone, and keeps it here.
            Only the build systems you give a token can install from it.</p>
        </div>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[
        ['libraries', <>Libraries <span className="registry-count">{libraries.length}</span></>],
        ['images', <>Images <span className="pill neutral">Coming soon</span></>],
        ['access', <>Who can install <span className="registry-count">{live}</span></>],
      ]} />
      {tab === 'libraries' && <Libraries libraries={libraries} manage={manage} onChange={reload} />}
      {tab === 'images' && <Images />}
      {tab === 'access' && (
        <Card pad title="Who can install" hint="Each build system or person gets its own token, which can only read this registry"
          action={manage && <button className="btn small" onClick={() => setMaking(true)}><Icon.key size={14} /> New token</button>}>
          <Tokens tokens={data.tokens} manage={manage} onChange={reload} />
        </Card>
      )}
      {making && <NewToken onClose={() => { setMaking(false); reload() }} />}
    </div>
  )
}

function Libraries({ libraries, manage, onChange }) {
  const [query, setQuery] = useState('')
  const [lang, setLang] = useState('all')
  const [state, setState] = useState('all')
  const [open, setOpen] = useState(null)
  const langs = Object.values(LANGUAGES).map((name) => [name, libraries.filter((l) => l.language === name).length])
  for (const l of libraries) if (!langs.some(([name]) => name === l.language)) langs.push([l.language, libraries.filter((x) => x.language === l.language).length])
  const needle = query.trim().toLowerCase()
  const shown = libraries.filter((l) => (lang === 'all' || l.language === lang) && (state === 'all' || l.state === state)
    && (!needle || `${l.name} ${l.summary} ${l.advisories.join(' ')} ${l.latest?.version || ''}`.toLowerCase().includes(needle)))
  const filtered = needle || lang !== 'all' || state !== 'all'
  const clear = () => { setQuery(''); setLang('all'); setState('all') }
  const selected = open && libraries.find((l) => l.key === open)
  return (
    <Card>
      <div className="registry-tools">
        <label className="registry-search">
          <Icon.search size={15} />
          <input className="input" type="search" placeholder="Search by library, flaw or advisory" value={query}
            onChange={(e) => setQuery(e.target.value)} aria-label="Search libraries" />
        </label>
        <div className="registry-chips" role="group" aria-label="Language">
          <button type="button" className={lang === 'all' ? 'on' : ''} onClick={() => setLang('all')}>All languages <span>{libraries.length}</span></button>
          {langs.map(([name, count]) => (
            <button key={name} type="button" className={lang === name ? 'on' : ''} disabled={!count}
              title={count ? undefined : `No ${name} libraries are fixed yet`} onClick={() => setLang(name)}>
              {name} <span>{count}</span>
            </button>
          ))}
        </div>
        <div className="registry-chips" role="group" aria-label="Status">
          {STATES.map(([k, label]) => <button key={k} type="button" className={state === k ? 'on' : ''} onClick={() => setState(k)}>{label}</button>)}
        </div>
      </div>
      {!libraries.length
        ? <Empty title="Nothing fixed yet">Fixed libraries appear here once WhiteHats has built and proved them.</Empty>
        : !shown.length
          ? <Empty title="No libraries match" action={filtered && <button className="btn small" onClick={clear}>Clear the filters</button>}>Try another name, language or advisory.</Empty>
          : (
            <div className="tbl-wrap tbl-stack"><table className="t registry-table">
              <thead><tr><th>Library</th><th>Language</th><th>Fixed version</th><th>Fixes</th><th>Status</th><th>Built</th></tr></thead>
              <tbody>{shown.map((l) => (
                <tr key={l.key} className="click" onClick={() => setOpen(l.key)}>
                  <td data-label="Library">
                    <button type="button" className="registry-name" onClick={(e) => { e.stopPropagation(); setOpen(l.key) }}>{l.name}</button>
                    <div className="muted registry-summary">{l.summary}</div>
                  </td>
                  <td data-label="Language">{l.language}<div className="muted registry-sub">{l.ecosystem}</div></td>
                  <td data-label="Fixed version">{l.latest
                    ? <><span className="mono">{l.latest.version}</span><div className="muted registry-sub">from {l.latest.upstream_version}</div></>
                    : <span className="muted">Not built yet</span>}</td>
                  <td data-label="Fixes"><div className="registry-tags">{l.advisories.map((id) => <span key={id} className="pill neutral">{id}</span>)}</div></td>
                  <td data-label="Status"><StatePill state={l.state} /></td>
                  <td data-label="Built" className="num">{l.latest ? fmt.ago(l.latest.created_at) : <span className="muted">-</span>}</td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
      {selected && (
        <Modal wide title={`${selected.name} (${selected.language})`} onClose={() => setOpen(null)}>
          <div className="stack" style={{ gap: 16 }}>
            {manage && selected.waiting.map((recipe) => <BuildCard key={recipe.key} recipe={recipe} onBuilt={onChange} />)}
            {!manage && selected.waiting.length > 0 && !selected.builds.length && (
              <p className="faint">A fixed version is ready to be built. Someone who manages connections can build and prove it.</p>
            )}
            {selected.builds.map((p) => <PackageCard key={p.id} pkg={p} />)}
          </div>
        </Modal>
      )}
    </Card>
  )
}

function StatePill({ state }) {
  if (state === 'ready') return <span className="pill ok">Ready to install</span>
  if (state === 'waiting') return <span className="pill warn">Waiting for a build</span>
  return <span className="pill crit">Build failed</span>
}

function Images() {
  return (
    <Card>
      <Empty title="Images are coming soon"><span className="registry-soon">
        The container images your services run on, rebuilt with the same fixes, proved the same way, and served to your
        build systems from this registry. Search and filter will work here the same way they do for libraries.</span>
      </Empty>
    </Card>
  )
}

function BuildCard({ recipe, onBuilt }) {
  const [busy, run] = useAction()
  const go = () => run('build', async () => {
    const out = await api('/api/registry/builds', { method: 'POST', body: { recipe: recipe.key } })
    onBuilt()
    return out
  }, (out) => `Built and proved ${out.name} ${out.version}`)
  return (
    <Card pad title={`${recipe.name} ${recipe.upstream_version} has a flaw with no fixed release`} hint={recipe.advisories.join(', ')}
      action={<button className="btn small primary" disabled={busy} onClick={go}>{busy ? 'Building and proving' : 'Build a fixed version'}</button>}>
      <p className="faint registry-note">WhiteHats fetches the release from PyPI, applies the upstream fix, and runs the proof on both in a sealed container with no network. Nothing is kept unless the proof sees the flaw before and not after.</p>
    </Card>
  )
}

function PackageCard({ pkg }) {
  const proof = pkg.proof || {}
  return (
    <Card pad title={`${pkg.name} ${pkg.version}`} hint={`Built ${fmt.ago(pkg.created_at)} by ${pkg.built_by} from ${pkg.name} ${pkg.upstream_version}`}
      action={<span className={`pill ${pkg.status === 'proved' ? 'ok' : 'crit'}`}>{pkg.status === 'proved' ? 'Proved' : pkg.status}</span>}>
      <div className="registry-package">
        <div className="registry-flaw">
          <div className="registry-tags">{pkg.advisories.map((id) => <span key={id} className="pill neutral">{id}</span>)}</div>
          <p>{pkg.summary}</p>
          <p className="faint">The fix: <a href={pkg.fix_source} target="_blank" rel="noreferrer">the upstream project's own change</a>, applied to {pkg.name} {pkg.upstream_version}.</p>
        </div>
        <div className="registry-proof" role="list" aria-label="Proof">
          <ProofRow label="Before" side={proof.before} />
          <ProofRow label="After" side={proof.after} />
        </div>
        <details className="registry-more">
          <summary>The change and the file</summary>
          <CodeBlock title="The change" text={pkg.patch} />
          <p className="faint registry-file">{pkg.filename}, {Math.round(pkg.size / 1024)} KB, sha256 <code>{pkg.sha256}</code></p>
        </details>
        <CodeBlock title="Install it (uv, in pyproject.toml)" text={
`[[tool.uv.index]]
name = "whitehats"
url = "${indexUrl()}"
explicit = true

[tool.uv.sources]
${pkg.name.toLowerCase()} = { index = "whitehats" }

# In CI: UV_INDEX_WHITEHATS_USERNAME=token and UV_INDEX_WHITEHATS_PASSWORD=<your registry token>`} />
      </div>
    </Card>
  )
}

function ProofRow({ label, side }) {
  if (!side) return null
  const good = label === 'Before' ? !side.passed : side.passed
  return (
    <div className="registry-proof-row" role="listitem">
      <span className={`pill ${good ? (side.passed ? 'ok' : 'warn') : 'crit'}`}>{label}: {side.passed ? 'passes' : 'flaw seen'}</span>
      <span>{side.said}</span>
    </div>
  )
}

function Tokens({ tokens, manage, onChange }) {
  const [busy, run] = useAction()
  if (!tokens.length) return <p className="faint">No one can install from the registry yet.</p>
  const revoke = (t) => run(t.id, async () => { await api(`/api/registry/tokens/${t.id}`, { method: 'DELETE' }); onChange() }, `Revoked ${t.name}`)
  return (
    <ul className="registry-tokens">
      {tokens.map((t) => (
        <li key={t.id} className={t.revoked || t.expired ? 'revoked' : ''}>
          <div><b>{t.name}</b> <span className="faint">ending {t.hint}, made by {t.created_by} {fmt.ago(t.created_at)}</span></div>
          <div className="faint">{t.revoked ? 'Revoked' : t.expired ? 'Expired' : t.last_used_at ? `Last used ${fmt.ago(t.last_used_at)}` : 'Not used yet'}</div>
          {manage && !t.revoked && !t.expired && <button className="btn small" disabled={busy} onClick={() => revoke(t)}>Revoke</button>}
        </li>
      ))}
    </ul>
  )
}

function NewToken({ onClose }) {
  const [name, setName] = useState('')
  const [made, setMade] = useState(null)
  const [busy, run] = useAction()
  const make = () => run('make', async () => setMade(await api('/api/registry/tokens', { method: 'POST', body: { name } })))
  return (
    <Modal title="New registry token" onClose={onClose} footer={made
      ? <button className="btn primary" onClick={onClose}>Done</button>
      : <button className="btn primary" disabled={busy || !name.trim()} onClick={make}>Make the token</button>}>
      {made
        ? <><p>Copy it now: it is shown only once. It can read the registry and nothing else.</p><CodeBlock title={made.name} text={made.token} /></>
        : <Field label="Who will use it" help="For example: proving-ground CI"><input id="registry-token-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus /></Field>}
    </Modal>
  )
}
