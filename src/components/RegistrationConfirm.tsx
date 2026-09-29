import { useEffect, useRef, useState } from 'react';
import { Archive, LoaderCircle, Trash2, X } from 'lucide-react';
import type { Environment } from '../types';

interface Props {
  action: 'archive' | 'delete';
  environment: Environment;
  submitting: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (confirmName?: string) => Promise<boolean>;
}

export default function RegistrationConfirm({ action, environment, submitting, error, onCancel, onConfirm }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const [confirmName, setConfirmName] = useState('');
  const [attempted, setAttempted] = useState(false);
  const deleting = action === 'delete';
  const unavailable = environment.running || environment.busy;
  useEffect(() => { dialog.current?.showModal(); cancelButton.current?.focus(); }, []);
  return <dialog ref={dialog} className="confirm-dialog registration-dialog" data-testid={`${action}-environment-dialog`}
    aria-labelledby="registration-confirm-title" onCancel={event => { event.preventDefault(); if (!submitting) onCancel(); }}>
    <button className="icon-button dialog-close" disabled={submitting} aria-label={deleting ? '取消删除登记' : '取消归档'} onClick={onCancel}><X size={19} /></button>
    <div className="confirm-icon">{deleting ? <Trash2 size={23} /> : <Archive size={23} />}</div>
    <h2 id="registration-confirm-title">{deleting ? '删除环境登记？' : '归档这个环境？'}</h2>
    <p className="registration-environment-name">{environment.name}</p>
    <p>{deleting ? '从工作台移除，保留本地浏览器和下载文件，工作台内无法恢复。'
      : '归档后停止参与批量打开、批量关闭和登录态维护。本地浏览器、登录信息与下载数据会保留，可在“已归档”中恢复。'}</p>
    {deleting ? <label className="create-field" htmlFor="delete-environment-name"><span>输入环境名称以确认：{environment.name}</span>
      <input id="delete-environment-name" data-testid="delete-environment-name" value={confirmName} disabled={submitting}
        autoComplete="off" spellCheck={false} onChange={event => setConfirmName(event.target.value)} /></label> : null}
    {unavailable ? <p className="dialog-error" role="alert">请先等待当前操作完成并关闭环境，再{deleting ? '删除登记' : '归档'}。</p> : null}
    {attempted && error ? <p className="dialog-error" role="alert">{error}</p> : null}
    <footer><button ref={cancelButton} className="button button-outline" disabled={submitting} onClick={onCancel}>取消</button>
      <button className={`button ${deleting ? 'button-danger' : 'button-primary'}`} data-testid={`${action}-environment-confirm`}
        disabled={submitting || unavailable || (deleting && confirmName !== environment.name)}
        onClick={() => { setAttempted(true); void onConfirm(deleting ? confirmName : undefined); }}>
        {submitting ? <><LoaderCircle size={14} className="spin" />正在提交…</> : deleting ? '删除登记' : '确认归档'}
      </button>
    </footer>
  </dialog>;
}
