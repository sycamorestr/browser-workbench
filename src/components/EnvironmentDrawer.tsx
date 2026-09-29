import { useEffect, useRef, useState } from 'react';
import { Archive, ArchiveRestore, Check, Copy, FolderOpen, Globe2, Power, Save, Trash2, X } from 'lucide-react';
import { formatTime, type Action, type Environment, type LoginCheckSettings } from '../types';
import { AuthStatus, RunningStatus } from './EnvironmentStatus';
import LoginCheckPanel from './LoginCheckPanel';

interface Props {
  environment: Environment;
  pending: boolean;
  archived?: boolean;
  onDismiss: () => void;
  onAction: (action: Action, ids: string[]) => void;
  onClose: (ids: string[]) => void;
  onSaveLoginCheck: (environmentId: string, settings: LoginCheckSettings) => Promise<LoginCheckSettings>;
  onArchive: (id: string) => void;
  onRestore: (id: string) => void;
  onDelete: (id: string) => void;
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

export default function EnvironmentDrawer({ environment, pending, archived = false, onDismiss, onAction, onClose, onSaveLoginCheck, onArchive, onRestore, onDelete }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  const disabled = pending || environment.busy;
  const identity = typeof environment.auth.identity === 'string' ? environment.auth.identity :
    environment.auth.identity ? JSON.stringify(environment.auth.identity, null, 2) : '';
  const urls = environment.tabs?.filter(tab => tab.url && tab.url !== 'about:blank') ?? [];
  const configuredUrls = Object.entries(environment.configured_urls ?? {}).filter(([role]) => role !== 'home' || !environment.home_url);
  const cookieSync = environment.cookie_sync;
  const sessionStatus = cookieSync ? { idle: '尚未保存', saved: '已保存', partial: '部分保存', error: '保存失败' }[cookieSync.status] : '状态未读取';
  return <dialog ref={dialog} className="environment-drawer" aria-labelledby="drawer-title" onCancel={event => { event.preventDefault(); onDismiss(); }}
    onClick={event => { if (event.target === event.currentTarget) onDismiss(); }}>
    <div className="drawer-content">
      <header className="drawer-header"><div><span className="section-label">环境详情</span><h2 id="drawer-title">{environment.name}</h2><p>{environment.id}</p></div>
        <button className="icon-button" autoFocus aria-label="关闭环境详情" onClick={onDismiss}><X size={20} /></button></header>
      <div className="drawer-body">
        {archived ? <div className="archived-detail-note" data-testid="archived-detail-note"><strong>已归档</strong><p>不参与批量操作和定时维护。恢复后可启动环境、检查登录或修改设置。</p>
          {environment.archived_at ? <small>归档于 {formatTime(environment.archived_at, true)}</small> : null}</div> : null}
        <div className="drawer-status"><RunningStatus environment={environment} pending={pending} /><AuthStatus environment={environment} />
          <span>{environment.tabs_count === null ? '标签数未知' : `${environment.tabs_count} 个标签页`}</span></div>
        <div className="drawer-section"><h3>连接信息</h3>
          <CopyField label="CDP endpoint" value={environment.cdp_endpoint} />
          <CopyField label="调试端口" value={String(environment.debug_port ?? '')} />
          <CopyField label="Profile" value={environment.profile_directory} />
        </div>
        <div className="drawer-section"><h3>登录与页面</h3>
          <div className="plain-detail"><span>最近检查</span><strong>{formatTime(environment.auth.checked_at, true)}</strong></div>
          {environment.auth.message ? <p className={`auth-message ${['verified', 'assumed'].includes(environment.auth.status) ? '' : 'auth-message-warning'}`}>{environment.auth.message}</p> : null}
          {identity ? <CopyField label="当前登录信息" value={identity} /> : null}
          {environment.home_url ? <CopyField label="主页地址" value={environment.home_url} /> : null}
          <div className="detail-field"><label>当前站点</label>
            {urls.length ? <ul className="page-list">{urls.map((tab, index) => <li key={`${tab.url}-${index}`}><Globe2 size={15} /><div><strong>{tab.title || '浏览器页面'}</strong><span>{tab.url}</span></div></li>)}</ul> :
              <p className="quiet-value">{environment.current_site || environment.current_url || '尚未读取页面信息'}</p>}
          </div>
          {configuredUrls.length ? <div className="detail-field"><label>配置页面</label>
            <ul className="page-list">{configuredUrls.map(([role, url]) => <li key={role}><Globe2 size={15} /><div><strong>{role === 'home' ? '主页地址' : '配置页面'}</strong><span>{url}</span></div></li>)}</ul>
          </div> : null}
        </div>
        {archived ? <div className="drawer-section"><h3>登录检查设置</h3><p className="login-check-help">设置已保留，恢复环境后可修改和检查登录。</p>
          <div className="plain-detail"><span>检查方式</span><strong>{environment.login_check?.mode === 'platform' ? '平台接口验证' : '通用网址检查（推定）'}</strong></div>
          <CopyField label="登录页地址" value={environment.login_check?.login_url ?? ''} />
        </div> : <LoginCheckPanel key={environment.id} environment={environment} pending={disabled} onSave={onSaveLoginCheck}
          onCheck={() => onAction('check-login', [environment.id])} />}
        <div className="drawer-section session-save-section"><h3>登录态保存</h3>
          <div className="plain-detail"><span>保存状态</span><strong className={cookieSync?.status === 'saved' ? 'green-text' : cookieSync?.status === 'partial' || cookieSync?.status === 'error' ? 'danger-text' : undefined}>{sessionStatus}</strong></div>
          <div className="plain-detail session-save-time"><span>最近保存</span><strong>{cookieSync?.last_saved_at ? formatTime(cookieSync.last_saved_at, true) : '尚未保存'}</strong></div>
          {cookieSync?.message ? <p className={`auth-message ${cookieSync.status === 'saved' ? '' : 'auth-message-warning'}`}>{cookieSync.message}</p> : null}
          {cookieSync && cookieSync.failed_count > 0 ? <p className="session-save-counts">有 {cookieSync.failed_count} 项会话数据尚未保存，请重试。</p> : null}
          <p className="session-save-description">关闭前自动保存登录态，再次打开时复用同一用户目录。平台使登录信息失效时，仍需重新登录。</p>
          <button className="button button-outline" disabled={disabled || archived || !environment.running} title={archived ? '请先恢复环境' : !environment.running ? '请先打开环境' : undefined}
            onClick={() => onAction('save-session', [environment.id])}><Save size={15} />保存登录态</button>
        </div>
        <div className="drawer-section"><h3>本机文件</h3>
          <CopyField label="用户目录" value={environment.user_data_dir} />
          <CopyField label="配置文件" value={environment.config_path} />
          <CopyField label="下载目录" value={environment.download_dir} />
          <CopyField label="浏览器程序" value={environment.executable_path ?? ''} />
          <div className="folder-actions"><button className="button button-outline" disabled={disabled} onClick={() => onAction('open-folder', [environment.id])}><FolderOpen size={15} />环境目录</button>
            <button className="button button-outline" disabled={disabled} onClick={() => onAction('open-results', [environment.id])}><FolderOpen size={15} />结果目录</button></div>
        </div>
        <div className="drawer-section environment-registration-section"><h3>{archived ? '归档管理' : '环境归档'}</h3>
          <p className="login-check-help">{archived ? '恢复后回到活动环境。删除登记会从工作台移除，本地浏览器和下载文件继续保留，工作台内无法恢复。'
            : '归档后保留本地登录与下载数据，并停止参与批量操作和定时维护。'}</p>
          {environment.running ? <p className="login-check-unsaved">请先关闭环境，再{archived ? '删除登记' : '归档'}。</p> : null}
          <button className={`button ${archived ? 'button-danger-quiet' : 'button-outline'}`} disabled={disabled || environment.running}
            data-testid={archived ? 'delete-environment-open' : 'archive-environment-open'}
            onClick={() => archived ? onDelete(environment.id) : onArchive(environment.id)}>
            {archived ? <><Trash2 size={14} />删除登记</> : <><Archive size={14} />归档环境</>}
          </button>
        </div>
      </div>
      <footer className="drawer-footer"><button className="button button-danger-quiet" disabled={disabled || !environment.running} onClick={() => onClose([environment.id])}><Power size={15} />关闭环境</button>
        {archived ? <button className="button button-primary" data-testid="restore-environment-detail" disabled={disabled} onClick={() => onRestore(environment.id)}><ArchiveRestore size={14} />恢复环境</button>
          : <button className="button button-primary" disabled={disabled} onClick={() => onAction(environment.running ? 'focus' : 'start', [environment.id])}>{environment.running ? '定位窗口' : '打开环境'}</button>}</footer>
    </div>
  </dialog>;
}
