import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, endSupport, getProjectSlug, getToken, inSupport, setProjectSlug, setToken, setUnauthorizedHandler, useApi } from './lib/api'
import { Avatar, Icon, Loading, Logo, ToastProvider } from './components/ui'
import Login, { Invite, Terms } from './pages/Login'
import Overview from './pages/Overview'
import Assistant from './pages/Assistant'
import Approvals, { FixDetail } from './pages/Approvals'
import Incidents, { IncidentDetail } from './pages/Incidents'
import FixFactory from './pages/FixFactory'
import Collectors, { CollectorDetail } from './pages/Collectors'
import Logs from './pages/Logs'
import Network from './pages/Network'
import Agents from './pages/Agents'
import Models from './pages/Models'
import Reports from './pages/Reports'
import Settings from './pages/Settings'
import Setup from './pages/Setup'
import Help from './pages/Help'
import Operator, { CompanyDetail, NewCompany, OperatorAudit } from './pages/Operator'
import AccountHome from './pages/AccountHome'
import ProjectSettings from './pages/ProjectSettings'

// The canvas engine and the layout worker load only when someone opens the Studio.
const Studio = lazy(() => import('./pages/Studio'))
const ImportReview = lazy(() => import('./pages/ImportReview'))
const Registry = lazy(() => import('./pages/Registry'))
const Compliance = lazy(() => import('./pages/Compliance'))
const Connectors = lazy(() => import('./pages/Connectors'))
const Audits = lazy(() => import('./pages/Audits'))
const Sphere = lazy(() => import('./pages/Sphere'))

const AuthCtx = createContext(null)
export const useAuth = () => useContext(AuthCtx)

// The list already fetched this session. Opening a project uses it at once, so the switcher does not show the bare slug while the next fetch is on its way.
let knownProjects = null
export function rememberProjects(rows) { knownProjects = rows }
export function cachedProjects() { return knownProjects }
function forgetProjects() { knownProjects = null }
/** True when the server says this person holds the permission (spec 0A). The server checks again on every call. */
export const allowed = (user, permission) => Boolean(user?.permissions?.includes(permission))

const TENANT_NAV = [
  ['Today', [['/', 'Overview', 'home'], ['/audits', 'Audits', 'search'], ['/assistant', 'Ask Big Hat', 'chat'], ['/approvals', 'Approvals', 'check', 'approvals']]],
  ['Respond', [['/incidents', 'Incidents', 'alert'], ['/fix-factory', 'Fix Factory', 'wrench'], ['/registry', 'Registry', 'layers']]],
  ['Visibility', [['/studio', 'Digital Twin', 'boundary'], ['/sphere', 'Knowledge Sphere', 'network'], ['/compliance', 'Compliance Sphere', 'check'], ['/connectors', 'Connectors', 'plug'], ['/collectors', 'Collectors', 'pod'], ['/agents', 'WhiteHats', 'swarm']]],
  ['Platform', [['/setup', 'Setup', 'flag', 'setup'], ['/models', 'Models and cost control', 'brain'], ['/reports', 'Board report', 'report'], ['/settings', 'Settings', 'settings'], ['/help', 'Help', 'book']]],
]
const LOCAL_PROJECT_NAV = [
  ['Today', [['/', 'Overview', 'home'], ['/audits', 'Audits', 'search']]],
  ['Respond', [['/incidents', 'Incidents', 'alert'], ['/fix-factory', 'Fix Factory', 'wrench'], ['/registry', 'Registry', 'layers']]],
  ['Visibility', [['/studio', 'Digital Twin', 'boundary'], ['/sphere', 'Knowledge Graph', 'network'], ['/compliance', 'Compliance Knowledge', 'check'], ['/connectors', 'Connectors', 'plug']]],
  ['Platform', [['/reports', 'Board Report', 'report'], ['/settings', 'Project settings', 'settings'], ['/help', 'Help', 'book']]],
]
const OPERATOR_NAV = [
  ['WhiteHats operations', [['/', 'Companies', 'building'], ['/companies/new', 'New company', 'plus'], ['/audit', 'Platform audit', 'report'], ['/help', 'Help', 'book']]],
]

// [title, subtitle, help page]
const TITLES = {
  '/': ['Overview', 'Where we stand today', 'developers/architecture'], '/assistant': ['Ask Big Hat', 'Plain-English answers from your own data', 'operations/ai-models'],
  '/approvals': ['Approvals', 'Nothing changes without a person saying yes', null], '/incidents': ['Incidents', 'What happened and what we are doing', null],
  '/fix-factory': ['Fix Factory', 'Security fixes, proven before anyone merges', 'connectors/github'],
  '/registry': ['Registry', 'Fixed builds of libraries with no fixed release', null],
  '/audits': ['Audits', 'Your whole code and cloud, audited by specialist agents', null],
  '/collectors': ['Collectors', 'What your collectors send: their logs, and the network they see', 'deploy/collectors'],
  '/agents': ['WhiteHats', 'The specialist swarm', 'developers/adding-a-whitehat'],
  '/compliance': ['Compliance Sphere', 'Control findings and supporting evidence', null],
  '/connectors': ['Connectors', 'Provider access and coverage', null],
  '/models': ['Models and cost control', 'Which models run, for what, at what cost', 'operations/ai-models'],
  '/studio': ['Clone Studio', 'Your estate as a map WhiteHats can reason about', 'operations/clone-studio'],
  '/sphere': ['Knowledge Sphere', 'What WhiteHats has learned your business depends on, from your approvers', null],
  '/studio/import': ['Import', 'Read in, review, apply', 'operations/clone-studio'],
  '/reports': ['Board report', 'Cyber risk in one page', null], '/settings': ['Settings', 'People, plan and audit trail', null],
  '/setup': ['Setup', 'From signed contract to live service', 'operations/tenant-onboarding'], '/help': ['Help', 'Operations and developer guides', null],
}
const OP_TITLES = {
  '/': ['Companies', 'WhiteHats operations', 'operations/tenant-onboarding'],
  '/companies': ['Company', 'Readiness, first value and health', 'operations/tenant-onboarding'],
  '/companies/new': ['New company', 'Contract, settings and WhiteHats', 'operations/tenant-onboarding#stage-1-contract-and-legal'],
  '/audit': ['Platform audit', 'Everything operators did', null], '/help': ['Help', 'Operations and developer guides', null],
}

function Shell({ nav, titles, children, badges = {}, project = null }) {
  const { user, org, logout, support, stopSupport } = useAuth()
  const loc = useLocation()
  const navigate = useNavigate()
  const [menu, setMenu] = useState(false)
  useEffect(() => setMenu(false), [loc.pathname])
  const screen = project ? projectPath(loc.pathname, project.slug) : loc.pathname
  const base = '/' + (screen.split('/')[1] || '')
  const [title, sub, doc] = titles[screen] || titles[base] || ['WhiteHats', '', null]
  return (
    <div className={`shell${org?.local_workspace ? " local-workspace" : ""}`}>
      <nav className={`nav ${menu ? 'open' : ''} ${org?.is_platform ? 'operator' : ''}`} aria-label="Main">
        <div className="brand"><div className="brand-mark"><Logo /></div><div><b>WhiteHats</b><span>{org?.is_platform ? 'Operator console' : org?.local_workspace ? "Enterprise workspace" : org?.name}</span></div>
          <button className="menu-btn" aria-expanded={menu} aria-label="Menu" onClick={() => setMenu((m) => !m)}>{menu ? '×' : '☰'}</button></div>
        <div className="nav-body">
          {nav.map(([group, items]) => (
            <div className="nav-group" key={group}>
              <div className="nav-label">{group}</div>
              {items.map(([to, label, ico, b]) => {
                const I = Icon[ico] || Icon.home
                if (b === 'setup' && !badges.setupVisible) return null
                const exact = to === '/' || (project && to === `/p/${project.slug}`)
                return <NavLink key={to} to={to} end={exact}><I />{label}{b && badges[b] > 0 && <span className="count">{badges[b]}</span>}</NavLink>
              })}
            </div>
          ))}
          <div className="nav-foot">
            <div className="person-identity"><Avatar name={user?.name} email={org?.local_workspace ? user?.email : undefined} /><div>
              <div className="who">{user?.name}</div>
              <div>{support ? 'WhiteHats support (read-only)' : user?.title || user?.role}</div>
            </div></div>
            {support ? <button onClick={stopSupport}>End support session</button> : <button onClick={logout}>Sign out</button>}
          </div>
        </div>
      </nav>
      <div className="main">
        {support && <div className="support-bar" role="status"><span>Read-only support session in <b>{org?.name}</b>. Every page you open is recorded in their audit trail.</span><button onClick={stopSupport}>End session</button></div>}
        <header className="topbar"><div><h1>{title}</h1><div className="sub">{sub}</div></div><div className="spacer" />
          {project && <ProjectSwitch project={project} pathname={loc.pathname} navigate={navigate} />}
          {doc && <Link className="btn small" to={`/help/${doc}`}><Icon.book size={14} /> How this works</Link>}
          {org?.local_workspace && !org.is_platform && <Link className="enterprise-link" to="/" aria-label={org.name + ' projects'}>
            {org.slug === 'acme' ? <span className="enterprise-mark" aria-hidden="true"><span /><span /><span /></span> : <Avatar name={org.name} />}
            <b>{org.name}</b>
          </Link>}
        </header>
        <main className="page">{children}</main>
      </div>
    </div>
  )
}

const ACCOUNT_NAV = [
  ['Account', [['/', 'Projects', 'building'], ['/settings', 'Account settings', 'settings'], ['/help', 'Help', 'book']]],
]
const ACCOUNT_TITLES = {
  '/': ['Projects', 'Every project in this account', null],
  '/settings': TITLES['/settings'],
  '/help': TITLES['/help'],
}
const PROJECT_TITLES = {
  ...TITLES,
  '/settings': ['Project settings', 'Name, people, connections and archive', null],
}
const LEGACY = ['sphere', 'assistant', 'approvals', 'incidents', 'fix-factory', 'collectors', 'logs', 'network', 'agents', 'studio', 'setup', 'models', 'compliance', 'connectors', 'reports']

function projectPath(pathname, slug) {
  const prefix = `/p/${slug}`
  if (pathname === prefix) return '/'
  if (pathname.startsWith(`${prefix}/`)) return pathname.slice(prefix.length)
  return pathname
}

function projectNav(slug, local) {
  const nav = (local ? LOCAL_PROJECT_NAV : TENANT_NAV).map(([group, items]) => [group, items.map(([to, label, ico, badge]) => {
    if (to === '/settings') return [`/p/${slug}/settings`, 'Project settings', ico, badge]
    if (to === '/help') return [to, label, ico, badge]
    return [`/p/${slug}${to === '/' ? '' : to}`, label, ico, badge]
  }).concat(!local && group === 'Platform' ? [['/settings', 'Account settings', 'building']] : [])])
  nav[0][1].unshift(['/', 'Projects', 'building'])
  return nav
}

function ProjectSwitch({ project, pathname, navigate }) {
  const prefix = `/p/${project.slug}`
  const rest = pathname.startsWith(prefix) ? pathname.slice(prefix.length) : ''
  // Until the list arrives the switcher is a placeholder of the same size, never the bare slug.
  if (!project.options) {
    return <div className="project-switch" role="status" aria-label="Loading projects"><span>Project</span><div className="skeleton project-switch-wait" /></div>
  }
  return (
    <label className="project-switch">
      <span>Project</span>
      <select className="input" aria-label="Project" value={project.slug}
        onChange={(e) => navigate(`/p/${e.target.value}${rest}`)}>
        {project.options.map((item) => <option key={item.slug} value={item.slug}>{item.name}</option>)}
      </select>
    </label>
  )
}

function TenantBanners() {
  const { setup, org, project_archived: archived } = useAuth()
  const loc = useLocation()
  const onSetup = loc.pathname === '/setup' || loc.pathname.endsWith('/setup')
  return (
    <>
      {archived && <div className="banner warn slim" role="status"><div><b>This project is archived, so it is read-only.</b> Nothing here can be changed and no connection feeds it. An Account owner or Account admin can restore it from <Link to="/">your projects</Link>.</div></div>}
      {org?.sample && !org.local_workspace && <div className="banner info slim" role="status"><div>Connect a source to see your organization’s evidence.</div></div>}
      {setup && setup.status !== 'live' && !onSetup && <div className="banner info" style={{ marginBottom: 16 }}><div><b>Your company is being set up.</b> {setup.passed} of {setup.total} checks are done. <Link to="/setup">Open your checklist</Link></div></div>}
    </>
  )
}

function ProjectScreens({ slug }) {
  // These paths are the remainder after /p/<slug>. A descendant <Routes> never sees the parent prefix.
  return (
    <Routes>
      <Route path="/" element={<Overview />} />
      <Route path="/assistant" element={<Assistant />} />
      <Route path="/approvals" element={<Approvals />} />
      <Route path="/approvals/:id" element={<FixDetail />} />
      <Route path="/incidents" element={<Incidents />} />
      <Route path="/incidents/:id" element={<IncidentDetail />} />
      <Route path="/fix-factory" element={<FixFactory />} />
      <Route path="/collectors" element={<Collectors />} />
      <Route path="/registry" element={<Suspense fallback={<Loading rows={4} />}><Registry /></Suspense>} />
      <Route path="/audits" element={<Suspense fallback={<Loading rows={4} />}><Audits /></Suspense>} />
      <Route path="/sphere" element={<Suspense fallback={<Loading rows={6} />}><Sphere /></Suspense>} />
      <Route path="/collectors/:id" element={<CollectorDetail />} />
      <Route path="/collectors/logs" element={<Logs />} />
      <Route path="/collectors/network" element={<Network />} />
      <Route path="/logs" element={<MovedUnderCollectors slug={slug} to="logs" />} />
      <Route path="/network" element={<MovedUnderCollectors slug={slug} to="network" />} />
      <Route path="/agents" element={<Agents />} />
      <Route path="/studio/import" element={<Suspense fallback={<Loading rows={6} />}><ImportReview /></Suspense>} />
      <Route path="/studio" element={<Suspense fallback={<div className="studio studio-empty"><Loading rows={6} /></div>}><Studio /></Suspense>} />
      <Route path="/setup" element={<Setup />} />
      <Route path="/models" element={<Models />} />
      <Route path="/connectors" element={<Suspense fallback={<Loading rows={4} />}><ConnectorsRoute slug={slug} /></Suspense>} />
      <Route path="/compliance" element={<Suspense fallback={<Loading rows={4} />}><Compliance /></Suspense>} />
      <Route path="/reports" element={<Reports />} />
      <Route path="/settings" element={<ProjectSettings />} />
      <Route path="/help/*" element={<Help />} />
      <Route path="*" element={<Navigate to={`/p/${slug}`} replace />} />
    </Routes>
  )
}

function ProjectApp() {
  const { slug } = useParams()
  const { setup, org, reload } = useAuth()
  setProjectSlug(slug)
  useEffect(() => { reload() }, [slug, reload])
  const counts = useApi('/api/overview', { poll: 60000 })
  const projects = useApi('/api/projects')
  if (projects.data) rememberProjects(projects.data)
  const arrived = projects.data ?? cachedProjects()
  const listed = arrived && arrived.some((item) => item.slug === slug)
  // An archived project is not in the list; an account role may still open it read-only, under its own name.
  const archived = useApi(projects.data && !listed ? '/api/projects/archived' : null)
  const own = archived.data?.find((item) => item.slug === slug)
  const options = listed ? arrived : projects.data ? [{ slug, name: own?.name || slug }, ...projects.data] : null
  const badges = { approvals: counts.data?.decisions?.length || 0, setup: setup?.open || 0, setupVisible: true }
  const local = org?.local_workspace
  const titles = local ? {
    ...PROJECT_TITLES,
    '/studio': ['Digital Twin', 'Services, infrastructure and their relationships', null],
    '/sphere': ['Knowledge Graph', 'Business concepts, rules and supporting evidence', null],
    '/compliance': ['Compliance Knowledge', 'Control findings and supporting evidence', null],
    '/reports': ['Board Report', 'Cyber risk in one page', null],
  } : PROJECT_TITLES
  return (
    <Shell nav={projectNav(slug, local)} titles={titles} badges={badges} project={{ slug, options }}>
      <TenantBanners />
      <ProjectScreens slug={slug} />
    </Shell>
  )
}

/** An old path such as /incidents opens the project the person is in, else Main, else the first they may open. */
function LegacyToProject() {
  const loc = useLocation()
  const current = getProjectSlug()
  const projects = useApi(current ? null : '/api/projects')
  if (projects.data) rememberProjects(projects.data)
  if (current) return <Navigate to={`/p/${current}${loc.pathname}${loc.search}${loc.hash}`} replace />
  if (projects.error) return <Navigate to="/" replace />
  if (!projects.data) return <Loading rows={4} />
  const rows = projects.data
  const slug = (rows.find((item) => item.slug === 'main') || rows[0])?.slug
  if (!slug) return <Navigate to="/" replace />
  return <Navigate to={`/p/${slug}${loc.pathname}${loc.search}${loc.hash}`} replace />
}

function AccountApp() {
  setProjectSlug('')
  return (
    <Shell nav={ACCOUNT_NAV} titles={ACCOUNT_TITLES}>
      <TenantBanners />
      <Routes>
        <Route path="/" element={<AccountHome />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/help/*" element={<Help />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  )
}

function TenantApp() {
  return (
    <Routes>
      <Route path="/prototype" element={<Navigate to="/" replace />} />
      <Route path="/p/:slug/*" element={<ProjectApp />} />
      {LEGACY.flatMap((name) => [
        <Route key={name} path={`/${name}`} element={<LegacyToProject />} />,
        <Route key={`${name}-rest`} path={`/${name}/*`} element={<LegacyToProject />} />,
      ])}
      <Route path="*" element={<AccountApp />} />
    </Routes>
  )
}

function OperatorApp() {
  setProjectSlug('')
  return (
    <Shell nav={OPERATOR_NAV} titles={OP_TITLES}>
      <Routes>
        <Route path="/" element={<Operator />} />
        <Route path="/companies/new" element={<NewCompany />} />
        <Route path="/companies/:id" element={<CompanyDetail />} />
        <Route path="/audit" element={<OperatorAudit />} />
        <Route path="/help/*" element={<Help />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  )
}

export default function App() {
  const [session, setSession] = useState(() => ({ ready: !getToken(), user: null }))
  // What the person may do depends on the project in view. Two loads can overlap (sign-in, then entering a project):
  // only the newest one's answer counts, or an older answer for Main would replace the project's own permissions.
  const latest = useRef(0)
  const load = useCallback(async () => {
    const mine = ++latest.current
    try {
      const d = await api('/api/auth/me')
      if (mine !== latest.current) return
      setSession({ ready: true, ...d })
    } catch (err) {
      // A navigation aborts the request. That is not a signed-out user, and clearing the token
      // would drop the session the next page needs.
      if (mine !== latest.current) return
      if (err?.status !== 401) {
        setSession((current) => (current.user ? current : { ready: true, user: null }))
        return
      }
      setToken(null)
      forgetProjects()
      setSession({ ready: true, user: null })
    }
  }, [])
  const logout = useCallback(() => { endSupport(); setToken(null); forgetProjects(); setSession({ ready: true, user: null }) }, [])
  useEffect(() => {
    setUnauthorizedHandler(() => { if (inSupport()) { endSupport(); forgetProjects(); load() } else logout() })
    if (getToken()) load()
  }, [load, logout])
  const login = (d) => { forgetProjects(); setToken(d.token); load() }
  const stopSupport = () => { endSupport(); window.location.assign('/') }
  if (!session.ready) return <div style={{ display: 'grid', placeItems: 'center', height: '100%' }}><span className="spin" /></div>
  const value = { ...session, login, logout, reload: load, stopSupport }
  return (
    <ToastProvider>
      <AuthCtx.Provider value={value}>
        <BrowserRouter>
          <Routes>
            <Route path="/invite/:token" element={<Invite />} />
            <Route path="*" element={
              !session.user ? <Login /> :
              session.needs_terms ? <Terms /> :
              session.org?.is_platform && !session.support ? <OperatorApp /> : <TenantApp />
            } />
          </Routes>
        </BrowserRouter>
      </AuthCtx.Provider>
    </ToastProvider>
  )
}

/**
 * The Connectors screen is gone: a connection is a node on the canvas (STUDIO.4). An old link, and where GitHub sends a person back to
 * after installing the App, open the Studio with that connection's node selected.
 */
/** Log analyzer and Network are tabs of Collectors now; old links and bookmarks land on the tab. */
function MovedUnderCollectors({ slug, to }) {
  const here = useLocation()
  return <Navigate to={`/p/${slug}/collectors/${to}${here.search}`} replace />
}

function ConnectorsRoute({ slug }) {
  const here = useLocation()
  const asked = new URLSearchParams(here.search)
  if (!asked.has('connection') && !asked.has('github')) return <Connectors />
  const next = new URLSearchParams()
  next.set('connection', asked.get('connection') || (asked.get('github') ? 'github' : 'aws'))
  for (const name of ['github', 'reason']) if (asked.get(name)) next.set(name, asked.get(name))
  return <Navigate to={`/p/${slug}/studio?${next}`} replace />
}
