import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { FolderOpen, MoreHorizontal, Power } from 'lucide-react';
import type { Action, Environment } from '../types';

interface Props {
  environment: Environment;
  disabled: boolean;
  onAction: (action: Action, ids: string[]) => void;
  onDetails: (id: string) => void;
  onClose: (ids: string[]) => void;
}

export default function EnvironmentActions({ environment, disabled, onAction, onDetails, onClose }: Props) {
  const [menu, setMenu] = useState<{ top: number; left: number } | null>(null);
  const anchor = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const dismiss = (event: PointerEvent) => {
      if (!panel.current?.contains(event.target as Node) && !anchor.current?.contains(event.target as Node)) setMenu(null);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setMenu(null); anchor.current?.focus(); } };
    const scroll = () => setMenu(null);
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', escape);
    window.addEventListener('scroll', scroll, true);
    return () => {
      document.removeEventListener('pointerdown', dismiss);
      document.removeEventListener('keydown', escape);
      window.removeEventListener('scroll', scroll, true);
    };
  }, [menu]);

  const selectAction = (action: Action) => { setMenu(null); onAction(action, [environment.id]); };
  return <div className="row-actions">
    <button className={`button button-small ${environment.running ? 'button-text' : 'button-outline'}`} disabled={disabled}
      onClick={() => onAction(environment.running ? 'focus' : 'start', [environment.id])}>
      {environment.running ? '定位' : '打开'}
    </button>
    <button className="button button-small button-text" disabled={disabled || !environment.running}
      title={!environment.running ? '请先打开环境' : undefined} onClick={() => onAction('check-login', [environment.id])}>检查登录</button>
    <button className="button button-small button-text muted-button" onClick={() => onDetails(environment.id)}>详情</button>
    <button ref={anchor} className="icon-button row-more" aria-label={`${environment.name}的更多操作`} aria-expanded={!!menu}
      aria-haspopup="menu" onClick={() => {
        if (menu) { setMenu(null); return; }
        const rect = anchor.current!.getBoundingClientRect();
        setMenu({ top: Math.min(rect.bottom + 6, window.innerHeight - 142), left: Math.max(8, rect.right - 186) });
      }}><MoreHorizontal size={18} /></button>
    {menu ? createPortal(<div ref={panel} className="action-menu" role="menu" style={menu}>
      <button role="menuitem" disabled={disabled} onClick={() => selectAction('open-folder')}><FolderOpen size={15} />打开环境目录</button>
      <button role="menuitem" disabled={disabled} onClick={() => selectAction('open-results')}><FolderOpen size={15} />打开结果目录</button>
      <div className="menu-divider" />
      <button role="menuitem" className="danger-text" disabled={disabled || !environment.running}
        onClick={() => { setMenu(null); onClose([environment.id]); }}><Power size={15} />关闭环境</button>
    </div>, document.body) : null}
  </div>;
}
