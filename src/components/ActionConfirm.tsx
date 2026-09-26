import { useEffect, useRef } from 'react';
import { Power, X } from 'lucide-react';
import type { Environment } from '../types';

interface Props { environments: Environment[]; submitting: boolean; onCancel: () => void; onConfirm: () => void }

export default function ActionConfirm({ environments, submitting, onCancel, onConfirm }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className="confirm-dialog" aria-labelledby="confirm-title"
    onCancel={event => { event.preventDefault(); if (!submitting) onCancel(); }}>
    <button className="icon-button dialog-close" disabled={submitting} aria-label="取消关闭" onClick={onCancel}><X size={19} /></button>
    <div className="confirm-icon"><Power size={23} /></div>
    <h2 id="confirm-title">关闭{environments.length > 1 ? `这 ${environments.length} 个环境` : '这个环境'}？</h2>
    <p>先保存所选环境的登录态，再关闭窗口；保存失败会保留窗口。再次打开将复用同一用户目录。正在使用中的环境不能关闭。</p>
    <ul className="confirm-names">{environments.map(environment => <li key={environment.id}>{environment.name}</li>)}</ul>
    <footer><button className="button button-outline" autoFocus disabled={submitting} onClick={onCancel}>取消</button>
      <button className="button button-danger" disabled={submitting || environments.some(environment => environment.busy)} onClick={onConfirm}>{submitting ? '正在提交…' : '确认关闭'}</button></footer>
  </dialog>;
}
