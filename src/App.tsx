import { useState } from 'react';
import { AlertCircle, ArrowUpRight, Check, CheckCheck, ChevronDown, Layers3, LoaderCircle, LockKeyhole, Monitor, PanelsTopLeft, Play, Plus, Power, RefreshCw, Search, X } from 'lucide-react';
import BrowserTable from './components/BrowserTable';
import EnvironmentDrawer from './components/EnvironmentDrawer';
import EnvironmentActions from './components/EnvironmentActions';
import ActionConfirm from './components/ActionConfirm';
import MaintenancePanel from './components/MaintenancePanel';
import CloseAllShopsDialog from './components/CloseAllShopsDialog';
import CreateShopDialog from './components/CreateShopDialog';
import { AuthStatus, CdpStatus, RunningStatus } from './components/EnvironmentStatus';
import { useWorkbench } from './hooks/useWorkbench';
import { actionNames, formatTime, type Action, type Environment } from './types';

type Filter = 'all' | 'running' | 'attention';
const needsAttention = (environment: Environment) => environment.cdp === 'conflict' ||
  (environment.running && environment.cdp !== 'connected') || ['required', 'error'].includes(environment.auth.status);

export default function App() {
  const { state, error, notice, refreshing, pendingIds, pendingStartJobs, submitting, refresh, runAction, dismissNotice,
    saveMaintenance, runMaintenance, maintenanceSaving, maintenanceStarting, closeAllShops, closeAllSubmitting, closeAllPending,
    createShop, pickFolder } = useWorkbench();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [detailId, setDetailId] = useState<string | null>(null);
  const [closingIds, setClosingIds] = useState<string[]>([]);
  const [closingAll, setClosingAll] = useState(false);
  const [creatingShop, setCreatingShop] = useState(false);
  const environments = state?.environments ?? [];
  const shops = environments.filter(environment => environment.kind === 'shop');
  const shared = environments.filter(environment => environment.kind === 'shared');
  const query = search.trim().toLocaleLowerCase();
  const visibleShops = shops.filter(environment => (!query || `${environment.name} ${environment.id}`.toLocaleLowerCase().includes(query)) &&
    (filter === 'all' || (filter === 'running' ? environment.running : needsAttention(environment))));
  const selectedEnvironments = shops.filter(environment => selected.has(environment.id));
  const selectionBusy = selectedEnvironments.some(environment => environment.busy || pendingIds.has(environment.id));
  const detail = environments.find(environment => environment.id === detailId);
  const closing = environments.filter(environment => closingIds.includes(environment.id));
  const activeJobs = state?.jobs.filter(job => job.status === 'running' || job.status === 'queued') ?? [];
  const allStartQueued = shops.length > 0 && [...pendingStartJobs, ...activeJobs.filter(job => job.action === 'start')]
    .some(job => shops.every(shop => job.ids.includes(shop.id)));
  const runningShopCount = shops.filter(shop => shop.running).length;
  const openingShops = [...pendingStartJobs, ...activeJobs.filter(job => job.action === 'start')]
    .some(job => shops.some(shop => job.ids.includes(shop.id)));
  const inventoryError = state?.inventory_error ?? state?.error?.message;
  const activity = [...(state?.activity ?? [])].sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime()).slice(0, 5);
  const onAction = (action: Action, ids: string[]) => { void runAction(action, ids); };
  const requestClose = (ids: string[]) => { setDetailId(null); setClosingIds(ids); };
  const toggleSelect = (id: string) => setSelected(previous => {
    const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next;
  });
  const selectAll = (checked: boolean) => setSelected(previous => {
    const next = new Set(previous); for (const environment of visibleShops) {
      if (checked) next.add(environment.id); else next.delete(environment.id);
    } return next;
  });

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#environments" aria-label="浏览器工作台首页"><span className="brand-icon"><PanelsTopLeft size={21} strokeWidth={1.8} /></span><span>浏览器工作台</span></a>
      <div className="nav-label">工作空间</div>
      <nav aria-label="主导航"><a href="#environments" className="nav-item active" aria-current="page"><Monitor size={18} /><span>浏览器环境</span></a></nav>
      <div className="sidebar-bottom"><span className="local-dot" /><span>仅本机访问</span><LockKeyhole size={13} /></div>
    </aside>
    <main id="environments" className="main-content">
      <header className="page-header"><div><h1>浏览器环境</h1><p>管理店铺登录环境与共享票聚</p></div>
        <div className="page-header-actions"><button className="button button-primary" data-testid="open-all-shops"
          disabled={!state || !shops.length || submitting || allStartQueued || closeAllPending} onClick={() => onAction('start', shops.map(shop => shop.id))}>
          {allStartQueued ? <LoaderCircle size={15} className="spin" /> : <Play size={15} />}{allStartQueued ? '正在打开全部店铺' : '一键打开全部店铺'}
        </button>
        <button className="button button-outline close-all-button" data-testid="close-all-shops"
          disabled={!state || !shops.length || closeAllPending || (!runningShopCount && !openingShops)} onClick={() => setClosingAll(true)}>
          {closeAllPending ? <LoaderCircle size={15} className="spin" /> : <Power size={15} />}{closeAllPending ? '正在关闭全部店铺' : `一键关闭全部店铺（${runningShopCount}）`}
        </button>
        <button className="button button-outline refresh-button" onClick={refresh} disabled={refreshing}>
          <RefreshCw size={15} className={refreshing ? 'spin' : undefined} />{refreshing && !state ? '正在连接' : '刷新状态'}
        </button></div></header>

      {error ? <div className="error-banner" role="alert"><AlertCircle size={18} /><div><strong>{state ? '操作需要处理' : '无法连接本地工作台'}</strong><p>{error}</p></div><button className="button button-small button-outline" onClick={refresh}>重试</button></div> : null}
      {inventoryError ? <div className="error-banner" role="alert"><AlertCircle size={18} /><div><strong>环境状态读取需要处理</strong><p>{inventoryError}</p></div><button className="button button-small button-outline" onClick={refresh}>刷新状态</button></div> : null}
      {notice ? <div className={`notice notice-${notice.tone}`} role="status">{notice.tone === 'success' ? <Check size={16} /> : <AlertCircle size={16} />}<span>{notice.message}</span><button className="icon-button" aria-label="收起操作结果" onClick={dismissNotice}><X size={15} /></button></div> : null}

      <section className="summary-strip" aria-label="环境概览">
        <div className="summary-item"><span className="summary-icon"><PanelsTopLeft size={20} /></span><div><p>总环境</p><strong>{state ? environments.length : '—'}</strong><span>个</span></div></div>
        <div className="summary-item"><span className="summary-icon green-icon"><Monitor size={20} /></span><div><p>运行中</p><strong>{state ? environments.filter(environment => environment.running).length : '—'}</strong><span>个</span></div></div>
        <div className="summary-item"><span className="summary-icon violet-icon"><CheckCheck size={21} /></span><div><p>已登录</p><strong>{state ? environments.filter(environment => environment.running && environment.auth.status === 'verified').length : '—'}</strong><span>个</span></div></div>
      </section>

      <MaintenancePanel maintenance={state?.maintenance} saving={maintenanceSaving} starting={maintenanceStarting}
        onSave={saveMaintenance} onRun={runMaintenance} />

      <section className="shared-section" aria-labelledby="shared-title"><div className="section-heading"><h2 id="shared-title">共享环境</h2><span>供各店铺共用</span></div>
        {!state ? <div className="loading-panel"><LoaderCircle size={18} className="spin" />正在读取环境…</div> : shared.length ? shared.map(environment => <div className="shared-panel" key={environment.id}>
          <div className="shared-name"><span className="shared-icon"><Layers3 size={23} strokeWidth={1.7} /></span><div><strong>{environment.name}</strong><p>共享票聚<span>·</span>{environment.id}</p></div></div>
          <div className="shared-status"><RunningStatus environment={environment} pending={pendingIds.has(environment.id)} /><CdpStatus environment={environment} /><AuthStatus environment={environment} /></div>
          <EnvironmentActions environment={environment} disabled={submitting || environment.busy || pendingIds.has(environment.id)} onAction={onAction} onDetails={setDetailId} onClose={requestClose} />
        </div>) : <div className="loading-panel">尚未配置共享环境。</div>}
      </section>

      <section className="shops-section" aria-labelledby="shops-title"><div className="section-heading"><h2 id="shops-title">店铺环境<span className="count-label">{state ? shops.length : '—'}</span></h2>
        <div className="shops-heading-actions"><span>每个店铺独立保存登录信息</span><button className="button button-small button-outline" data-testid="create-shop-open"
          disabled={!state} onClick={() => setCreatingShop(true)}><Plus size={14} />新建店铺</button></div></div>
        <div className="shops-panel"><div className="table-toolbar">
          <label className="search-field"><Search size={17} /><input aria-label="搜索店铺名称或环境ID" placeholder="搜索店铺名称或 ID" value={search} onChange={event => setSearch(event.target.value)} />
            {search ? <button className="icon-button" aria-label="清空搜索" onClick={() => setSearch('')}><X size={14} /></button> : null}</label>
          <div className="table-filters" role="group" aria-label="筛选环境状态">{([['all', '全部'], ['running', '运行中'], ['attention', '需处理']] as const).map(([value, label]) =>
            <button key={value} className={filter === value ? 'filter-button selected' : 'filter-button'} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</div>
        </div>
          {selectedEnvironments.length ? <div className="selection-toolbar"><span>已选择 <strong>{selectedEnvironments.length}</strong> 个环境</span><div>
            <button className="button button-small button-primary" disabled={submitting || selectionBusy} onClick={() => onAction('start', selectedEnvironments.map(environment => environment.id))}><Play size={13} />启动所选</button>
            <button className="button button-small button-outline" disabled={submitting || selectionBusy || selectedEnvironments.some(environment => !environment.running)}
              title={selectedEnvironments.some(environment => !environment.running) ? '请先打开所选环境' : undefined}
              onClick={() => onAction('check-login', selectedEnvironments.map(environment => environment.id))}>检查登录</button>
            <button className="button button-small button-danger-quiet" disabled={submitting || selectionBusy || selectedEnvironments.some(environment => !environment.running)}
              title={selectedEnvironments.some(environment => !environment.running) ? '仅可关闭运行中的环境' : undefined}
              onClick={() => requestClose(selectedEnvironments.map(environment => environment.id))}>关闭所选</button>
            <button className="button button-small button-text" onClick={() => setSelected(new Set())}>取消</button>
          </div></div> : null}
          {!state ? <div className="loading-panel table-loading"><LoaderCircle size={21} className="spin" />等待本机服务返回店铺列表</div> : <BrowserTable environments={visibleShops} selected={selected} pendingIds={pendingIds} submitting={submitting}
            onSelect={toggleSelect} onSelectAll={selectAll} onAction={onAction} onDetails={setDetailId} onClose={requestClose} />}
          <div className="table-footer"><span>{state ? `显示 ${visibleShops.length} / ${shops.length} 个店铺` : '正在读取店铺列表'}</span>
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
    {detail ? <EnvironmentDrawer environment={detail} pending={submitting || pendingIds.has(detail.id)} onDismiss={() => setDetailId(null)} onAction={onAction} onClose={requestClose} /> : null}
    {closing.length ? <ActionConfirm environments={closing} submitting={submitting} onCancel={() => setClosingIds([])}
      onConfirm={() => { void runAction('close', closingIds).then(submitted => { if (submitted) setClosingIds([]); }); }} /> : null}
    {closingAll ? <CloseAllShopsDialog shops={shops} submitting={closeAllSubmitting} error={error} onCancel={() => setClosingAll(false)}
      onConfirm={async pauseMaintenance => { const submitted = await closeAllShops(pauseMaintenance); if (submitted) setClosingAll(false); return submitted; }} /> : null}
    {creatingShop ? <CreateShopDialog defaultParentFolder={state?.creation_defaults?.parent_folder ?? ''} onCancel={() => setCreatingShop(false)}
      onCreated={() => { setCreatingShop(false); setSearch(''); setFilter('all'); }} onCreate={createShop} onPickFolder={pickFolder} /> : null}
  </div>;
}
