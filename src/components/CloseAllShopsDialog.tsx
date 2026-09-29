import { useEffect, useRef, useState } from 'react';
import { Power, X } from 'lucide-react';
import type { Environment } from '../types';

interface Props {
  environments: Environment[];
  submitting: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (pauseMaintenance: boolean) => Promise<boolean>;
}

export default function CloseAllShopsDialog({ environments, submitting, error, onCancel, onConfirm }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [pauseMaintenance, setPauseMaintenance] = useState(true);
  const [attempted, setAttempted] = useState(false);
  useEffect(() => { dialog.current?.showModal(); }, []);
  const running = environments.filter(environment => environment.running).length;
  return <dialog ref={dialog} className="confirm-dialog close-all-dialog" data-testid="close-all-dialog" aria-labelledby="close-all-title"
    onCancel={event => { event.preventDefault(); if (!submitting) onCancel(); }}>
    <button className="icon-button dialog-close" disabled={submitting} aria-label="取消关闭全部环境" onClick={onCancel}><X size={19} /></button>
    <div className="confirm-icon"><Power size={23} /></div>
    <h2 id="close-all-title">关闭全部环境？</h2>
    <p>共 {environments.length} 个浏览器环境，当前 {running} 个运行中。后台也会收尾已排队打开的环境。</p>
    <p>逐个保存登录态后关闭，保存失败会保留该窗口。当前维护轮处理完当前环境后停止。</p>
    <ul className="confirm-names close-all-names">{environments.map(environment => <li key={environment.id}><span>{environment.name}</span><small>{environment.running ? '运行中' : '未运行 / 等待操作'}</small></li>)}</ul>
    <label className="dialog-checkbox"><input type="checkbox" data-testid="close-all-pause-maintenance" checked={pauseMaintenance}
      disabled={submitting} onChange={event => setPauseMaintenance(event.target.checked)} /><span>同时暂停定时维护</span></label>
    <p className="dialog-help">勾选后暂停后续定时轮次；未勾选时，下次定时维护可能重新打开环境。</p>
    {attempted && error ? <p className="dialog-error" role="alert">{error}</p> : null}
    <footer><button className="button button-outline" autoFocus disabled={submitting} onClick={onCancel}>取消</button>
      <button className="button button-danger" data-testid="close-all-confirm" disabled={submitting}
        onClick={() => { setAttempted(true); void onConfirm(pauseMaintenance); }}>{submitting ? '正在提交…' : '保存并关闭全部环境'}</button></footer>
  </dialog>;
}
