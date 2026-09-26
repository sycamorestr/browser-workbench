import { useEffect, useState } from 'react';
import { CalendarClock, ChevronDown, LoaderCircle, Pause, RefreshCw } from 'lucide-react';
import { formatTime, type Maintenance, type MaintenanceSettings } from '../types';

interface Props {
  maintenance?: Maintenance;
  saving: boolean;
  starting: boolean;
  onSave: (settings: MaintenanceSettings) => Promise<boolean>;
  onRun: () => Promise<boolean>;
}

const intervals = [[30, '30 分钟'], [60, '1 小时'], [120, '2 小时'], [240, '4 小时'], [720, '12 小时'], [1440, '每天']] as const;
const statusLabels = { idle: '尚未执行', running: '维护中', complete: '已完成', partial: '部分环境需处理', failed: '维护失败', paused: '已暂停' };

export default function MaintenancePanel({ maintenance, saving, starting, onSave, onRun }: Props) {
  const [draft, setDraft] = useState<MaintenanceSettings>({ enabled: false, interval_minutes: 120, include_shared: true });
  const [editing, setEditing] = useState(false);
  const enabled = maintenance?.enabled ?? false;
  const interval = maintenance?.interval_minutes ?? 120;
  const includeShared = maintenance?.include_shared ?? true;
  useEffect(() => {
    if (!editing) setDraft({ enabled, interval_minutes: interval, include_shared: includeShared });
  }, [editing, enabled, interval, includeShared]);
  const update = (patch: Partial<MaintenanceSettings>) => { setEditing(true); setDraft(current => ({ ...current, ...patch })); };
  const dirty = draft.enabled !== enabled || draft.interval_minutes !== interval || draft.include_shared !== includeShared;
  const running = maintenance?.running ?? false;
  const unavailable = !maintenance;
  const resultRows = maintenance?.last_results ?? [];
  const failedCount = resultRows.filter(result => result.status === 'failed').length;
  const skippedCount = resultRows.filter(result => result.status === 'skipped').length;
  const pause = () => { void onSave({ enabled: false, interval_minutes: interval, include_shared: includeShared }); };

  return <section className="maintenance-panel" aria-labelledby="maintenance-title" data-testid="maintenance-panel">
    <header className="maintenance-heading"><div className="maintenance-title"><CalendarClock size={18} /><h2 id="maintenance-title">登录态维护</h2>
      <span className={`status ${enabled ? 'status-green' : 'status-neutral'}`}><i />{unavailable ? '等待读取' : enabled ? '定时已启用' : '定时已暂停'}</span>
      {running || starting ? <span className="maintenance-running"><LoaderCircle size={12} className="spin" />{running ? '正在维护' : '等待维护'}</span> : null}
    </div>
      {enabled ? <button className="button button-small button-text" disabled={saving || unavailable} onClick={pause}
        data-testid="pause-maintenance"><Pause size={12} />暂停定时维护</button> : null}
    </header>
    <p className="maintenance-description">会打开已关闭的店铺环境，刷新千牛主页并检查登录、保存会话。工作台后台需保持运行；平台登录失效时仍需重新登录。</p>
    <div className="maintenance-form">
      <label className="maintenance-switch"><input type="checkbox" role="switch" aria-label="启用定时维护" data-testid="maintenance-enabled"
        checked={draft.enabled} disabled={unavailable || saving} onChange={event => update({ enabled: event.target.checked })} /><span>定时维护</span></label>
      <label className="maintenance-interval" htmlFor="maintenance-interval">访问间隔<select id="maintenance-interval" value={draft.interval_minutes}
        disabled={unavailable || saving} onChange={event => update({ interval_minutes: Number(event.target.value) })}>{intervals.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="maintenance-checkbox"><input type="checkbox" aria-label="同时维护共享票聚" checked={draft.include_shared}
        disabled={unavailable || saving} onChange={event => update({ include_shared: event.target.checked })} /><span>同时维护共享票聚</span></label>
      <div className="maintenance-buttons"><button className="button button-small button-primary" data-testid="save-maintenance-settings"
        disabled={unavailable || saving || !dirty} onClick={() => { void onSave(draft).then(saved => { if (saved) setEditing(false); }); }}>{saving ? '正在保存…' : '保存设置'}</button>
        <button className="button button-small button-outline" data-testid="run-maintenance-now" disabled={unavailable || running || starting || saving} onClick={() => { void onRun(); }}>
          <RefreshCw size={13} className={running || starting ? 'spin' : undefined} />{running || starting ? '维护进行中' : '现在维护一次'}</button></div>
    </div>
    {dirty ? <p className="maintenance-unsaved">设置尚未保存；“现在维护一次”使用已保存的配置。</p> : null}
    <div className="maintenance-meta"><span>下次 <strong>{maintenance?.next_run_at ? formatTime(maintenance.next_run_at, true) : '未安排'}</strong></span>
      <span>上次 <strong>{maintenance?.last_run_at ? formatTime(maintenance.last_run_at, true) : '尚未执行'}</strong></span>
      <span>最近一轮 <strong>{maintenance ? statusLabels[maintenance.last_status] : '等待后台提供状态'}</strong></span>
    </div>
    {maintenance?.message ? <p className="maintenance-message" role="status">{maintenance.message}</p> : null}
    {running || starting ? <p className="maintenance-message">暂停后，当前定时轮次处理完当前店铺即停止；手动维护一次会完成本轮。</p> : null}
    {resultRows.length ? <details className="maintenance-results"><summary><span>逐店结果<span>{failedCount ? `${failedCount} 个失败` : '无失败'}{skippedCount ? ` · ${skippedCount} 个跳过` : ''}</span></span><ChevronDown size={14} /></summary>
      <ul>{resultRows.map((result, index) => <li key={`${result.id}-${index}`}><strong>{result.name || result.id}</strong><span className={`maintenance-result-status result-${result.status}`}>
        {{ complete: '已完成', skipped: '已跳过', failed: '失败' }[result.status]}</span><p>{result.message}</p></li>)}</ul>
    </details> : null}
  </section>;
}
