import { useEffect, useState } from 'react';
import { AlertCircle, Archive, ArrowUpRight, Check, CheckCheck, ChevronDown, LoaderCircle, LockKeyhole, Monitor, PanelsTopLeft, Play, Plus, Power, RefreshCw, Search, X } from 'lucide-react';
import BrowserTable from './components/BrowserTable';
import EnvironmentDrawer from './components/EnvironmentDrawer';
import ActionConfirm from './components/ActionConfirm';
import MaintenancePanel from './components/MaintenancePanel';
import CloseAllShopsDialog from './components/CloseAllShopsDialog';
import CreateShopDialog from './components/CreateShopDialog';
import StopWorkbenchDialog from './components/StopWorkbenchDialog';
import RegistrationConfirm from './components/RegistrationConfirm';
import ArchivedEnvironmentTable from './components/ArchivedEnvironmentTable';
import { useWorkbench } from './hooks/useWorkbench';
import { actionNames, formatTime, type Action, type Environment } from './types';

type Filter = 'all' | 'running' | 'logged-in' | 'attention';
const loggedIn = (environment: Environment) => environment.running && ['verified', 'assumed'].includes(environment.auth.status);
const needsAttention = (environment: Environment) => environment.cdp === 'conflict' ||
  (environment.running && environment.cdp !== 'connected') || ['required', 'error'].includes(environment.auth.status);

export default function App() {
  const { state, error, notice, refreshing, pendingIds, pendingStartJobs, submitting, refresh, runAction, dismissNotice,
    saveMaintenance, runMaintenance, maintenanceSaving, maintenanceStarting, closeAllShops, closeAllSubmitting, closeAllPending,
    createShop, pickFolder, saveLoginCheck, changeRegistration, registrationIds, stopWorkbench, shutdownStatus, shutdownError, shutdownMessage } = useWorkbench();
  const [environmentView, setEnvironmentView] = useState<'active' | 'archived'>('active');
  const [registrationConfirm, setRegistrationConfirm] = useState<{ action: 'archive' | 'delete'; id: string } | null>(null);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [detailId, setDetailId] = useState<string | null>(null);
  const [closingIds, setClosingIds] = useState<string[]>([]);
  const [closingAll, setClosingAll] = useState(false);
  const [creatingShop, setCreatingShop] = useState(false);
  const [stoppingWorkbench, setStoppingWorkbench] = useState(false);
  const environments = state?.environments ?? [];
  const archivedEnvironments = state?.archived_environments ?? [];
  const allEnvironments = [...environments, ...archivedEnvironments];
  const viewingArchived = environmentView === 'archived';
  const viewEnvironments = viewingArchived ? archivedEnvironments : environments;
  const query = search.trim().toLocaleLowerCase();
  const visibleEnvironments = viewEnvironments.filter(environment => (!query || `${environment.name} ${environment.id}`.toLocaleLowerCase().includes(query)) &&
    (viewingArchived || filter === 'all' || (filter === 'running' ? environment.running : filter === 'logged-in' ? loggedIn(environment) : needsAttention(environment))));
  const selectedEnvironments = environments.filter(environment => selected.has(environment.id));
  const selectionBusy = selectedEnvironments.some(environment => environment.busy || pendingIds.has(environment.id));
  const detail = allEnvironments.find(environment => environment.id === detailId);
  const detailArchived = archivedEnvironments.some(environment => environment.id === detailId);
  const closing = allEnvironments.filter(environment => closingIds.includes(environment.id));
  const registrationEnvironment = registrationConfirm ? allEnvironments.find(environment => environment.id === registrationConfirm.id) : undefined;
  const activeJobs = state?.jobs.filter(job => job.status === 'running' || job.status === 'queued') ?? [];
  const allStartQueued = environments.length > 0 && [...pendingStartJobs, ...activeJobs.filter(job => job.action === 'start')]
    .some(job => environments.every(environment => job.ids.includes(environment.id)));
  const runningCount = environments.filter(environment => environment.running).length;
  const openingEnvironments = [...pendingStartJobs, ...activeJobs.filter(job => job.action === 'start')]
    .some(job => environments.some(environment => job.ids.includes(environment.id)));
  const inventoryError = state?.inventory_error ?? state?.error?.message;
  const activity = [...(state?.activity ?? [])].sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime()).slice(0, 5);
  const onAction = (action: Action, ids: string[]) => { void runAction(action, ids); };
  const requestClose = (ids: string[]) => { setDetailId(null); setClosingIds(ids); };
  const requestRegistration = (action: 'archive' | 'delete', id: string) => { setDetailId(null); setRegistrationConfirm({ action, id }); };
  const restoreEnvironment = async (id: string) => {
    if (await changeRegistration('restore', id)) setDetailId(current => current === id ? null : current);
  };
  const switchView = (view: 'active' | 'archived') => {
    setEnvironmentView(view); setSearch(''); setFilter('all'); setSelected(new Set()); setDetailId(null);
  };
  useEffect(() => {
    const activeIds = new Set(state?.environments.map(environment => environment.id) ?? []);
    setSelected(previous => {
      const next = new Set([...previous].filter(id => activeIds.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }, [state?.environments]);
  const toggleSelect = (id: string) => setSelected(previous => {
    const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next;
  });
  const selectAll = (checked: boolean) => setSelected(previous => {
    const next = new Set(previous); for (const environment of visibleEnvironments) {
      if (checked) next.add(environment.id); else next.delete(environment.id);
    } return next;
  });

  if (shutdownStatus === 'stopping' || shutdownStatus === 'stopped') {
    const stopped = shutdownStatus === 'stopped';
    return <main className="workbench-shutdown-screen">
      <section className="workbench-shutdown-card" aria-labelledby="shutdown-title" aria-live="polite">
        <span className={`shutdown-status-icon ${stopped ? 'shutdown-complete' : ''}`}>
          {stopped ? <Power size={29} /> : <LoaderCircle size={29} className="spin" />}
        </span>
        <p className="shutdown-brand">浏览器工作台</p>
        <h1 id="shutdown-title">{stopped ? '工作台已停止' : '正在停止工作台'}</h1>
        <p>{stopped ? '后台服务已退出，定时维护与自动保存已停止。' : '正在完成已提交的操作；当前环境维护结束后退出后台。'}</p>
        <p>停止工作台会保留已打开的浏览器。</p>
        {shutdownMessage ? <p className="shutdown-message" role="status">{shutdownMessage}</p> : null}
        <div className="shutdown-restart-help"><strong>{stopped ? '需要再次使用时' : '请等待操作完成'}</strong>
          <p>{stopped ? '双击桌面「浏览器工作台」即可重新启动。现在可以关闭此页面。' : '工作台正在收尾，请保留此页面查看结果。'}</p>
        </div>
      </section>
    </main>;
  }

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#environments" aria-label="浏览器工作台首页" onClick={() => switchView('active')}><span className="brand-icon"><PanelsTopLeft size={21} strokeWidth={1.8} /></span><span>浏览器工作台</span></a>
      <div className="nav-label">工作空间</div>
      <nav aria-label="主导航"><a href="#environments" className="nav-item active" aria-current="page" onClick={() => switchView('active')}><Monitor size={18} /><span>浏览器环境</span></a></nav>
      <div className="sidebar-bottom"><span className="local-dot" /><span>仅本机访问</span><LockKeyhole size={13} /></div>
    </aside>
    <main id="environments" className="main-content">
      <header className="page-header"><div><h1>{viewingArchived ? '已归档环境' : '浏览器环境'}</h1><p>{viewingArchived ? '保留本地数据，按需恢复或移除工作台登记' : '统一管理独立浏览器，为 AI 与自动化提供连接'}</p></div>
        <div className="page-header-actions">{!viewingArchived ? <><button className="button button-primary" data-testid="open-all-shops"
          disabled={!state || !environments.length || submitting || registrationIds.length > 0 || allStartQueued || closeAllPending} onClick={() => onAction('start', environments.map(environment => environment.id))}>
          {allStartQueued ? <LoaderCircle size={15} className="spin" /> : <Play size={15} />}{allStartQueued ? '正在打开全部环境' : '一键打开全部环境'}
        </button>
        <button className="button button-outline close-all-button" data-testid="close-all-shops"
          disabled={!state || !environments.length || registrationIds.length > 0 || closeAllPending || (!runningCount && !openingEnvironments)} onClick={() => setClosingAll(true)}>
          {closeAllPending ? <LoaderCircle size={15} className="spin" /> : <Power size={15} />}{closeAllPending ? '正在关闭全部环境' : `一键关闭全部环境（${runningCount}）`}
        </button></> : null}
        <button className="button button-outline refresh-button" onClick={refresh} disabled={refreshing}>
          <RefreshCw size={15} className={refreshing ? 'spin' : undefined} />{refreshing && !state ? '正在连接' : '刷新状态'}
        </button>
        <button className="button button-outline stop-workbench-button" data-testid="stop-workbench"
          disabled={!state || shutdownStatus === 'requesting'} onClick={() => setStoppingWorkbench(true)}>
          <Power size={15} />停止工作台
        </button></div></header>

      <nav className="environment-view-switch" aria-label="环境登记状态">
        <button className={viewingArchived ? '' : 'selected'} data-testid="show-active-environments" aria-pressed={!viewingArchived} onClick={() => switchView('active')}><Monitor size={15} />活动环境<span>{environments.length}</span></button>
        <button className={viewingArchived ? 'selected' : ''} data-testid="show-archived-environments" aria-pressed={viewingArchived} onClick={() => switchView('archived')}><Archive size={15} />已归档<span>{archivedEnvironments.length}</span></button>
      </nav>

      {error ? <div className="error-banner" role="alert"><AlertCircle size={18} /><div><strong>{state ? '操作需要处理' : '无法连接本地工作台'}</strong><p>{error}</p></div><button className="button button-small button-outline" onClick={refresh}>重试</button></div> : null}
      {inventoryError ? <div className="error-banner" role="alert"><AlertCircle size={18} /><div><strong>环境状态读取需要处理</strong><p>{inventoryError}</p></div><button className="button button-small button-outline" onClick={refresh}>刷新状态</button></div> : null}
      {notice ? <div className={`notice notice-${notice.tone}`} role="status">{notice.tone === 'success' ? <Check size={16} /> : <AlertCircle size={16} />}<span>{notice.message}</span><button className="icon-button" aria-label="收起操作结果" onClick={dismissNotice}><X size={15} /></button></div> : null}

      {!viewingArchived ? <><section className="summary-strip" aria-label="活动环境概览">
        <div className="summary-item"><span className="summary-icon"><PanelsTopLeft size={20} /></span><div><p>活动环境</p><strong>{state ? environments.length : '—'}</strong><span>个</span></div></div>
        <div className="summary-item"><span className="summary-icon green-icon"><Monitor size={20} /></span><div><p>运行中</p><strong>{state ? environments.filter(environment => environment.running).length : '—'}</strong><span>个</span></div></div>
        <div className="summary-item" title="包含通过网址推定和平台接口验证的运行中环境"><span className="summary-icon violet-icon"><CheckCheck size={21} /></span><div><p>已登录（含推定）</p><strong>{state ? environments.filter(loggedIn).length : '—'}</strong><span>个</span></div></div>
      </section>

      <MaintenancePanel maintenance={state?.maintenance} saving={maintenanceSaving} starting={maintenanceStarting}
        onSave={saveMaintenance} onRun={runMaintenance} /></> : <p className="archived-view-help">归档环境不参与批量操作、登录统计和定时维护。恢复后可继续使用原有本地登录与下载数据。</p>}

      <section className="shops-section" aria-labelledby="shops-title"><div className="section-heading"><h2 id="shops-title">{viewingArchived ? '已归档列表' : '环境列表'}<span className="count-label">{state ? viewEnvironments.length : '—'}</span></h2>
        {!viewingArchived ? <div className="shops-heading-actions"><span>独立数据目录 · 固定 CDP 连接</span><button className="button button-small button-outline" data-testid="create-shop-open"
          disabled={!state} onClick={() => setCreatingShop(true)}><Plus size={14} />新建环境</button></div> : null}</div>
        <div className="shops-panel"><div className="table-toolbar">
          <label className="search-field"><Search size={17} /><input aria-label="搜索环境名称或ID" placeholder="搜索环境名称或 ID" value={search} onChange={event => setSearch(event.target.value)} />
            {search ? <button className="icon-button" aria-label="清空搜索" onClick={() => setSearch('')}><X size={14} /></button> : null}</label>
          {!viewingArchived ? <div className="table-filters" role="group" aria-label="筛选环境状态">{([['all', '全部'], ['running', '运行中'], ['logged-in', '已登录'], ['attention', '需处理']] as const).map(([value, label]) =>
            <button key={value} className={filter === value ? 'filter-button selected' : 'filter-button'} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</div> : null}
        </div>
          {!viewingArchived && selectedEnvironments.length ? <div className="selection-toolbar"><span>已选择 <strong>{selectedEnvironments.length}</strong> 个环境</span><div>
            <button className="button button-small button-primary" disabled={submitting || selectionBusy} onClick={() => onAction('start', selectedEnvironments.map(environment => environment.id))}><Play size={13} />启动所选</button>
            <button className="button button-small button-outline" disabled={submitting || selectionBusy || selectedEnvironments.some(environment => !environment.running)}
              title={selectedEnvironments.some(environment => !environment.running) ? '请先打开所选环境' : undefined}
              onClick={() => onAction('check-login', selectedEnvironments.map(environment => environment.id))}>检查登录</button>
            <button className="button button-small button-danger-quiet" disabled={submitting || selectionBusy || selectedEnvironments.some(environment => !environment.running)}
              title={selectedEnvironments.some(environment => !environment.running) ? '仅可关闭运行中的环境' : undefined}
              onClick={() => requestClose(selectedEnvironments.map(environment => environment.id))}>关闭所选</button>
            <button className="button button-small button-text" onClick={() => setSelected(new Set())}>取消</button>
          </div></div> : null}
          {!state ? <div className="loading-panel table-loading"><LoaderCircle size={21} className="spin" />等待本机服务返回环境列表</div> : viewingArchived ? <ArchivedEnvironmentTable environments={visibleEnvironments} pendingIds={pendingIds} submitting={submitting}
            onDetails={setDetailId} onRestore={id => { void restoreEnvironment(id); }} onDelete={id => requestRegistration('delete', id)} onClose={requestClose} /> : <BrowserTable environments={visibleEnvironments} selected={selected} pendingIds={pendingIds} submitting={submitting}
            onSelect={toggleSelect} onSelectAll={selectAll} onAction={onAction} onDetails={setDetailId} onClose={requestClose} />}
          <div className="table-footer"><span>{state ? `显示 ${visibleEnvironments.length} / ${viewEnvironments.length} 个${viewingArchived ? '归档' : ''}环境` : '正在读取环境列表'}</span>
            <span className="updated-at">{activeJobs.length || pendingIds.size ? <><LoaderCircle size={12} className="spin" />操作处理中</> : state ? `更新于 ${formatTime(state.updated_at)}` : ''}<ChevronDown size={12} className="footer-chevron" /></span></div>
        </div>
      </section>

      <section className="activity-section" aria-labelledby="activity-title"><div className="section-heading"><h2 id="activity-title">最近操作</h2><span>自动同步本机记录</span></div>
        {activity.length ? <div className="activity-list">{activity.map((item, index) => <div className="activity-row" key={item.id ?? `${item.at}-${item.environment_id}-${index}`}>
          <span className={`activity-dot ${item.level === 'error' ? 'activity-warning' : ''}`} />
          <time dateTime={item.at}>{formatTime(item.at)}</time><strong>{item.name || environments.find(environment => environment.id === item.environment_id)?.name || item.environment_id}</strong>
          <span className="activity-action">{actionNames[item.action as Action] ?? item.action}</span><p>{item.message}</p>
        </div>)}</div> : <p className="activity-empty">{state ? '暂无操作记录。打开一个环境开始使用。' : '连接后显示操作记录。'}</p>}
      </section>
      <footer className="page-footer"><span><LockKeyhole size={12} />环境与登录信息保存在本机</span><span>浏览器工作台 <ArrowUpRight size={12} /></span></footer>
    </main>
    {detail ? <EnvironmentDrawer environment={detail} archived={detailArchived} pending={submitting || pendingIds.has(detail.id)} onDismiss={() => setDetailId(null)} onAction={onAction} onClose={requestClose} onSaveLoginCheck={saveLoginCheck}
      onArchive={id => requestRegistration('archive', id)} onRestore={id => { void restoreEnvironment(id); }} onDelete={id => requestRegistration('delete', id)} /> : null}
    {registrationConfirm && registrationEnvironment ? <RegistrationConfirm action={registrationConfirm.action} environment={registrationEnvironment}
      submitting={registrationIds.includes(registrationEnvironment.id)} error={error} onCancel={() => setRegistrationConfirm(null)}
      onConfirm={async confirmName => { const changed = await changeRegistration(registrationConfirm.action, registrationEnvironment.id, confirmName);
        if (changed) setRegistrationConfirm(null); return changed; }} /> : null}
    {closing.length ? <ActionConfirm environments={closing} submitting={submitting} onCancel={() => setClosingIds([])}
      onConfirm={() => { void runAction('close', closingIds).then(submitted => { if (submitted) setClosingIds([]); }); }} /> : null}
    {closingAll ? <CloseAllShopsDialog environments={environments} submitting={closeAllSubmitting} error={error} onCancel={() => setClosingAll(false)}
      onConfirm={async pauseMaintenance => { const submitted = await closeAllShops(pauseMaintenance); if (submitted) setClosingAll(false); return submitted; }} /> : null}
    {creatingShop ? <CreateShopDialog defaultParentFolder={state?.creation_defaults?.parent_folder ?? ''} onCancel={() => setCreatingShop(false)}
      onCreated={() => { setCreatingShop(false); setSearch(''); setFilter('all'); }} onCreate={createShop} onPickFolder={pickFolder} /> : null}
    {stoppingWorkbench ? <StopWorkbenchDialog submitting={shutdownStatus === 'requesting'} error={shutdownError}
      activeJobs={activeJobs.length} onCancel={() => setStoppingWorkbench(false)}
      onConfirm={async () => { const accepted = await stopWorkbench(); if (accepted) setStoppingWorkbench(false); return accepted; }} /> : null}
  </div>;
}
