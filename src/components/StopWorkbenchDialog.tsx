import { useEffect, useRef, useState } from 'react';
import { LoaderCircle, Power, X } from 'lucide-react';

interface Props {
  submitting: boolean;
  error: string | null;
  activeJobs: number;
  onCancel: () => void;
  onConfirm: () => Promise<boolean>;
}

export default function StopWorkbenchDialog({ submitting, error, activeJobs, onCancel, onConfirm }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const [attempted, setAttempted] = useState(false);
  useEffect(() => { dialog.current?.showModal(); cancelButton.current?.focus(); }, []);
  return <dialog ref={dialog} className="confirm-dialog stop-workbench-dialog" data-testid="stop-workbench-dialog"
    aria-labelledby="stop-workbench-title" aria-describedby="stop-workbench-description"
    onCancel={event => { event.preventDefault(); if (!submitting) onCancel(); }}>
    <button className="icon-button dialog-close" disabled={submitting} aria-label="取消停止工作台" onClick={onCancel}><X size={19} /></button>
    <div className="confirm-icon"><Power size={23} /></div>
    <h2 id="stop-workbench-title">停止工作台？</h2>
    <p id="stop-workbench-description">将退出本机后台服务，并停止定时维护与自动保存。已打开的浏览器会继续保留。</p>
    <p>{activeJobs ? `当前有 ${activeJobs} 个操作正在执行或排队。` : ''}已提交的操作会完成后再退出；当前环境维护结束后，其余维护停止。</p>
    <p className="dialog-help">如需同时关闭浏览器，请先取消并使用「一键关闭全部环境」。再次使用时，双击桌面「浏览器工作台」；原定时维护设置会继续生效。</p>
    {attempted && error ? <p className="dialog-error" role="alert">{error}</p> : null}
    <footer><button ref={cancelButton} className="button button-outline" autoFocus disabled={submitting} onClick={onCancel}>取消</button>
      <button className="button button-danger" data-testid="stop-workbench-confirm" disabled={submitting}
        onClick={() => { setAttempted(true); void onConfirm(); }}>
        {submitting ? <><LoaderCircle size={14} className="spin" />正在提交…</> : '停止工作台'}
      </button></footer>
  </dialog>;
}
