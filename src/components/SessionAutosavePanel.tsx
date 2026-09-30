import { useEffect, useState } from 'react';
import { Save } from 'lucide-react';
import { sessionAutosaveIntervals, type SessionAutosave, type SessionAutosaveSettings } from '../types';

interface Props {
  settings?: SessionAutosave;
  saving: boolean;
  disabled: boolean;
  onSave: (settings: SessionAutosaveSettings) => Promise<boolean>;
}

export default function SessionAutosavePanel({ settings, saving, disabled, onSave }: Props) {
  const enabled = settings?.enabled ?? true;
  const interval = settings?.interval_minutes ?? 5;
  const [draft, setDraft] = useState<SessionAutosaveSettings>({ enabled, interval_minutes: interval });
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    if (!editing) setDraft({ enabled, interval_minutes: interval });
  }, [editing, enabled, interval]);
  const unavailable = !settings;
  const blocked = unavailable || disabled || saving;
  const dirty = !unavailable && (draft.enabled !== enabled || draft.interval_minutes !== interval);
  const needsRepair = !unavailable && !!settings.message;
  const update = (patch: Partial<SessionAutosaveSettings>) => {
    setEditing(true);
    setDraft(current => ({ ...current, ...patch }));
  };

  return <section className="maintenance-panel session-autosave-panel" aria-labelledby="session-autosave-title" data-testid="session-autosave-panel">
    <header className="maintenance-heading"><div className="maintenance-title"><Save size={18} /><h2 id="session-autosave-title">自动保存会话</h2>
      <span className={`status ${!unavailable && enabled ? 'status-green' : 'status-neutral'}`}><i />
        {unavailable ? '暂不可用' : enabled ? `每 ${interval} 分钟` : '已关闭'}</span>
    </div></header>
    <p className="maintenance-description">工作台空闲时，为运行中的浏览器定时保存会话，不会打开已关闭的环境。此设置与登录态维护的访问间隔独立。</p>
    <div className="maintenance-form">
      <label className="maintenance-switch"><input type="checkbox" role="switch" aria-label="启用自动保存会话" data-testid="session-autosave-enabled"
        checked={draft.enabled} disabled={blocked} onChange={event => update({ enabled: event.target.checked })} /><span>定时自动保存</span></label>
      <label className="maintenance-interval" htmlFor="session-autosave-interval">保存间隔<select id="session-autosave-interval" value={draft.interval_minutes}
        disabled={blocked || !draft.enabled} onChange={event => update({ interval_minutes: Number(event.target.value) })}>
        {sessionAutosaveIntervals.map(value => <option key={value} value={value}>{value === 60 ? '1 小时' : `${value} 分钟`}</option>)}</select></label>
      <div className="maintenance-buttons"><button className="button button-small button-primary" data-testid="save-session-autosave-settings"
        disabled={blocked || (!dirty && !needsRepair)} onClick={() => { void onSave(draft).then(saved => { if (saved) setEditing(false); }); }}>{saving ? '正在保存…' : '保存设置'}</button></div>
    </div>
    {dirty ? <p className="maintenance-unsaved">设置尚未保存，点击“保存设置”后生效。</p>
      : needsRepair ? <p className="maintenance-unsaved">设置需要重新保存，可直接点击“保存设置”，无需先切换开关。</p> : null}
    <p className="maintenance-message session-autosave-help">关闭后，仍可手动保存；检查登录、通过工作台关闭环境及登录态维护时的会话保存不受影响。</p>
    {unavailable ? <p className="maintenance-message" role="status">当前后台尚未提供自动保存设置，请等待连接或更新工作台后重试。</p>
      : settings.message ? <p className="maintenance-message" role="status">{settings.message}</p> : null}
  </section>;
}
