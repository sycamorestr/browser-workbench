import { useEffect, useRef, useState } from 'react';
import { Check, Copy, FolderOpen, Globe2, Power, Save, X } from 'lucide-react';
import { formatTime, type Action, type Environment } from '../types';
import { AuthStatus, RunningStatus } from './EnvironmentStatus';

interface Props {
  environment: Environment;
  pending: boolean;
  onDismiss: () => void;
  onAction: (action: Action, ids: string[]) => void;
  onClose: (ids: string[]) => void;
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => { setCopied(false); setFailed(false); }, [value]);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1800);
    return () => clearTimeout(timer);
  }, [copied]);
  return <div className="detail-field"><label>{label}</label><div className="copy-value"><code>{value || '未提供'}</code>
    <button className="icon-button" disabled={!value} title={copied ? '已复制' : `复制${label}`} aria-label={`复制${label}`}
      onClick={async () => { try { await navigator.clipboard.writeText(value); setCopied(true); setFailed(false); } catch { setFailed(true); } }}>
      {copied ? <Check size={15} className="green-text" /> : <Copy size={15} />}
    </button></div>{failed ? <small className="danger-text">复制未完成，请手动选择内容复制。</small> : null}</div>;
}

export default function EnvironmentDrawer({ environment, pending, onDismiss, onAction, onClose }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  const disabled = pending || environment.busy;
  const identity = typeof environment.auth.identity === 'string' ? environment.auth.identity :
    environment.auth.identity ? JSON.stringify(environment.auth.identity, null, 2) : '';
  const urls = environment.tabs?.filter(tab => tab.url && tab.url !== 'about:blank') ?? [];
  const cookieSync = environment.cookie_sync;
  const sessionStatus = cookieSync ? { idle: '尚未保存', saved: '已保存', partial: '部分保存', error: '保存失败' }[cookieSync.status] : '状态未读取';
  return <dialog ref={dialog} className="environment-drawer" aria-labelledby="drawer-title" onCancel={event => { event.preventDefault(); onDismiss(); }}
    onClick={event => { if (event.target === event.currentTarget) onDismiss(); }}>
    <div className="drawer-content">
      <header className="drawer-header"><div><span className="section-label">环境详情</span><h2 id="drawer-title">{environment.name}</h2><p>{environment.id}</p></div>
        <button className="icon-button" autoFocus aria-label="关闭环境详情" onClick={onDismiss}><X size={20} /></button></header>
      <div className="drawer-body">
        <div className="drawer-status"><RunningStatus environment={environment} pending={pending} /><AuthStatus environment={environment} />
          <span>{environment.tabs_count === null ? '标签数未知' : `${environment.tabs_count} 个标签页`}</span></div>
        <div className="drawer-section"><h3>登录与页面</h3>
          <div className="plain-detail"><span>最近检查</span><strong>{formatTime(environment.auth.checked_at, true)}</strong></div>
          {environment.auth.message ? <p className={`auth-message ${environment.auth.status === 'verified' ? '' : 'auth-message-warning'}`}>{environment.auth.message}</p> : null}
          {identity ? <CopyField label="当前登录信息" value={identity} /> : null}
          <div className="detail-field"><label>当前站点</label>
            {urls.length ? <ul className="page-list">{urls.map((tab, index) => <li key={`${tab.url}-${index}`}><Globe2 size={15} /><div><strong>{tab.title || '业务页面'}</strong><span>{tab.url}</span></div></li>)}</ul> :
              <p className="quiet-value">{environment.current_site || environment.current_url || '尚未读取页面信息'}</p>}
          </div>
          {environment.configured_urls && Object.keys(environment.configured_urls).length ? <div className="detail-field"><label>配置页面</label>
            <ul className="page-list">{Object.entries(environment.configured_urls).map(([role, url]) => <li key={role}><Globe2 size={15} /><div><strong>{{ home: '千牛主页', invoice: '千牛发票', orders: '已卖出宝贝', goods: '票聚商品管理' }[role] ?? role}</strong><span>{url}</span></div></li>)}</ul>
          </div> : null}
        </div>
        <div className="drawer-section session-save-section"><h3>登录态保存</h3>
          <div className="plain-detail"><span>保存状态</span><strong className={cookieSync?.status === 'saved' ? 'green-text' : cookieSync?.status === 'partial' || cookieSync?.status === 'error' ? 'danger-text' : undefined}>{sessionStatus}</strong></div>
          <div className="plain-detail session-save-time"><span>最近保存</span><strong>{cookieSync?.last_saved_at ? formatTime(cookieSync.last_saved_at, true) : '尚未保存'}</strong></div>
          {cookieSync?.message ? <p className={`auth-message ${cookieSync.status === 'saved' ? '' : 'auth-message-warning'}`}>{cookieSync.message}</p> : null}
          {cookieSync && cookieSync.failed_count > 0 ? <p className="session-save-counts">有 {cookieSync.failed_count} 项会话数据尚未保存，请重试。</p> : null}
          <p className="session-save-description">关闭前自动保存登录态，再次打开时复用同一用户目录。平台使登录信息失效时，仍需重新登录。</p>
          <button className="button button-outline" disabled={disabled || !environment.running} title={!environment.running ? '请先打开环境' : undefined}
            onClick={() => onAction('save-session', [environment.id])}><Save size={15} />保存登录态</button>
        </div>
        <div className="drawer-section"><h3>连接信息</h3>
          <CopyField label="CDP endpoint" value={environment.cdp_endpoint} />
          <CopyField label="调试端口" value={String(environment.debug_port ?? '')} />
          <CopyField label="Profile" value={environment.profile_directory} />
        </div>
        <div className="drawer-section"><h3>本机文件</h3>
          <CopyField label="用户目录" value={environment.user_data_dir} />
          <CopyField label="配置文件" value={environment.config_path} />
          <CopyField label="下载目录" value={environment.download_dir} />
          <CopyField label="浏览器程序" value={environment.executable_path ?? ''} />
          <div className="folder-actions"><button className="button button-outline" disabled={disabled} onClick={() => onAction('open-folder', [environment.id])}><FolderOpen size={15} />环境目录</button>
            <button className="button button-outline" disabled={disabled} onClick={() => onAction('open-results', [environment.id])}><FolderOpen size={15} />结果目录</button></div>
        </div>
      </div>
      <footer className="drawer-footer"><button className="button button-danger-quiet" disabled={disabled || !environment.running} onClick={() => onClose([environment.id])}><Power size={15} />关闭环境</button>
        <button className="button button-primary" disabled={disabled} onClick={() => onAction(environment.running ? 'focus' : 'start', [environment.id])}>{environment.running ? '定位窗口' : '打开环境'}</button></footer>
    </div>
  </dialog>;
}
