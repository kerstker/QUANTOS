import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from '@/lib/router';
import { fetchResearchRuns } from '@/lib/api/research-console-api';
import { fetchRunnerHealth } from '@/lib/api/research-runner-api';
import { CONSOLE_MODULES } from '@/workbench/module-registry';
import { Detail, Forward, Launch, Overview, Runs } from './Research';
import { Modules, System } from './Modules';
import { Empty, Header, Icon, StudioBoundary } from './components';
import { useResource } from './data';
import { Compare } from './Compare';
import {Market,Analysis,Models,Operations} from './Analysis';
import {Factory} from './Factory';
import {FactoryLifecycle} from './FactoryLifecycle';
import { NAV, LEGACY } from './routes';
export default function Studio() {
  const location = useLocation(), nav = useNavigate();
  const [menu, setMenu] = useState(false);
  const [search, setSearch] = useState('');
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem('quantos.studio.theme') ?? 'light';
    }
    catch {
      return 'light';
    }
  });
  const registry = useResource('studio:runs', fetchResearchRuns, 10000);
  const runner = useResource('studio:runner', fetchRunnerHealth, 30000);
  useEffect(() => {
    document.documentElement.dataset.studioTheme = theme;
    try {
      localStorage.setItem('quantos.studio.theme', theme);
    }
    catch { /* preference is optional */ }
  }, [theme]);
  useEffect(() => {
    setMenu(false);
    document.title = 'QUANTOS · Research Studio';
    const module = CONSOLE_MODULES.find(m => location.pathname === `/research/${m.id}`);
    const redirect = LEGACY[location.pathname] ?? (module ? `/studio/modules/${module.id}` : null);
    if (redirect)
      nav(redirect, { replace: true });
  }, [location.pathname, nav]);
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        document.getElementById('studio-search')?.focus();
      }
      if (e.key === 'Escape')
        setMenu(false);
    };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, []);
  const path = LEGACY[location.pathname] ?? location.pathname;
  const runs = { rows: registry.data?.runs ?? [], loading: registry.loading, error: registry.error, available: registry.data !== null && registry.data.registry_available !== false, refresh: registry.refresh };
  let page;
  if (path === '/')
    page = <Overview runs={runs} health={runner.data} />;
  else if (path === '/research/runs')
    page = <Runs runs={runs} key={location.search} initialQuery={new URLSearchParams(location.search).get('query') ?? ''} />;
  else if (path.startsWith('/research/runs/'))
    page = <Detail id={path.slice('/research/runs/'.length)} />;
  else if (path === '/studio/new')
    page = <Launch key={location.search} health={runner.data} initialKind={new URLSearchParams(location.search).get('kind') ?? ''} initialAttempt={new URLSearchParams(location.search).get('attempt')??'1'} initialAction={new URLSearchParams(location.search).get('action')??'0'} />;
  else if (path === '/studio/factory')
    page = <Factory/>;
  else if (path === '/studio/market')
    page = <Market/>;
  else if (path === '/studio/lifecycle')
    page = <FactoryLifecycle/>;
  else if (path === '/studio/analysis')
    page = <Analysis runs={runs} initialRun={new URLSearchParams(location.search).get('run')??''}/>;
  else if (path === '/studio/models')
    page = <Models runs={runs}/>;
  else if (path === '/studio/operations')
    page = <Operations runs={runs}/>;
  else if (path === '/studio/compare')
    page = <Compare runs={runs}/>;
  else if (path === '/studio/forward')
    page = <Forward runs={runs} />;
  else if (path === '/studio/modules' || path.startsWith('/studio/modules/'))
    page = <Modules selectedId={path.split('/')[3]} key={path.split('/')[3]} />;
  else if (path === '/studio/system')
    page = <System health={runner.data} error={runner.error} refresh={runner.refresh} />;
  else
    page = <>
      <Header eyebrow="404 / NOT FOUND" title="页面不存在" />
      <Empty title="这个地址没有对应的工作区" action={<Link className="s-button primary" to="/">返回研究工作台</Link>}>请从左侧导航重新选择。</Empty>
    </>;
  return <div className="studio">
    <a className="s-skip" href="#studio-main">跳到主要内容</a>
    <aside className={`s-sidebar ${menu ? 'open' : ''}`} id="studio-navigation">
      <Link className="s-brand" to="/" aria-label="QUANTOS 首页">
        <span className="s-logomark">Q<span />
        </span>
        <span>QUANTOS<small>RESEARCH STUDIO</small>
        </span>
      </Link>
      <div className="s-workspace-label">
        <span className="s-workspace-avatar">Q</span>
        <div>量化研究空间<small>BTC · Alpha Factory</small>
        </div>
        <span className="s-online-dot" />
      </div>
      <p className="s-nav-caption">WORKSPACE</p>
      <nav aria-label="主导航">{NAV.map(([to, title, icon]) => {
        const active = to === '/' ? path === '/' : path.startsWith(to);
        const workspace = new URLSearchParams(location.search).get('workspace');
        const target = workspace && ['/studio/factory', '/studio/lifecycle'].includes(to)
          ? `${to}?${new URLSearchParams({workspace})}` : to;
        return <Link key={to} to={target} className={active ? 'active' : ''} aria-current={active ? 'page' : undefined}>
          <Icon name={icon} />
          <span>{title}</span>{active && <span className="s-nav-dot" />}</Link>;
      })}</nav>
      <div className="s-sidebar-bottom">
        <div className="s-sidebar-note">
          <span className="s-online-dot" />
          <span>{runner.data ? '研究引擎已连接' : '研究引擎未连接'}</span>
        </div>
        <Link to="/studio/system" className="s-profile">
          <span className="s-avatar">R</span>
          <span>Research workspace<small>本地研究环境</small>
          </span>
          <Icon name="arrow" />
        </Link>
      </div>
    </aside>{menu && <button className="s-menu-backdrop" aria-label="关闭导航" onClick={() => setMenu(false)} />}
    <div className="s-body">
      <header className="s-topbar">
        <button className="s-icon-button s-menu-button" aria-label="展开导航" aria-expanded={menu} aria-controls="studio-navigation" onClick={() => setMenu(!menu)}>
          <Icon name="menu" />
        </button>
        <div className="s-breadcrumb">工作空间 <span>/</span> <strong>{NAV.find(([to]) => to !== '/' && path.startsWith(to))?.[1] ?? (path === '/studio/new' ? '新建研究' : '研究工作台')}</strong>
        </div>
        <form className="s-global-search" onSubmit={e => { e.preventDefault(); nav(`/research/runs?query=${encodeURIComponent(search)}`); }}>
          <Icon name="search" />
          <input id="studio-search" aria-label="全局搜索运行" placeholder="搜索运行…" value={search} onChange={e => setSearch(e.target.value)} />
          <kbd>⌘ K</kbd>
        </form>
        <span className="s-mode" title="L2_DRY_RUN_ONLY">
          <i />研究模式</span>
        <button className="s-icon-button" aria-label={theme === 'light' ? '切换深色模式' : '切换浅色模式'} onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>
          <Icon name="moon" />
        </button>
      </header>
      <main id="studio-main" className="s-main" tabIndex={-1}>
        <StudioBoundary key={path}>{page}</StudioBoundary>
        <footer className="s-footer">
          <span>QUANTOS <span>Research Studio</span>
          </span>
          <span>真实记录 · 可复核研究</span>
        </footer>
      </main>
    </div>
  </div>;
}
