import { useEffect, useRef } from 'react';
import { Monitor, SearchX } from 'lucide-react';
import type { Action, Environment } from '../types';
import { AuthStatus, CdpStatus, RunningStatus } from './EnvironmentStatus';
import EnvironmentActions from './EnvironmentActions';

interface Props {
  environments: Environment[];
  selected: Set<string>;
  pendingIds: Set<string>;
  submitting: boolean;
  onSelect: (id: string) => void;
  onSelectAll: (checked: boolean) => void;
  onAction: (action: Action, ids: string[]) => void;
  onDetails: (id: string) => void;
  onClose: (ids: string[]) => void;
}

export default function BrowserTable(props: Props) {
  const { environments, selected, pendingIds, submitting, onSelect, onSelectAll, onAction, onDetails, onClose } = props;
  const all = environments.length > 0 && environments.every(environment => selected.has(environment.id));
  const some = environments.some(environment => selected.has(environment.id));
  const selectAll = useRef<HTMLInputElement>(null);
  useEffect(() => { if (selectAll.current) selectAll.current.indeterminate = some && !all; }, [some, all]);

  return <div className="table-scroll">
    <table className="browser-table">
      <thead><tr>
        <th className="check-column"><input ref={selectAll} type="checkbox" aria-label="选择当前筛选的所有环境"
          checked={all} disabled={!environments.length} onChange={event => onSelectAll(event.target.checked)} /></th>
        <th>浏览器环境</th><th>运行状态</th><th>CDP</th><th>登录状态</th><th className="actions-heading">操作</th>
      </tr></thead>
      <tbody>{environments.map(environment => <tr key={environment.id} className={selected.has(environment.id) ? 'selected-row' : undefined}>
        <td className="check-column"><input type="checkbox" checked={selected.has(environment.id)}
          aria-label={`选择${environment.name}`} onChange={() => onSelect(environment.id)} /></td>
        <td><button className="environment-name" onClick={() => onDetails(environment.id)}>
          <span className="environment-avatar"><Monitor size={19} strokeWidth={1.7} /></span>
          <span><strong>{environment.name}</strong><small>{environment.id}</small></span>
        </button></td>
        <td><RunningStatus environment={environment} pending={pendingIds.has(environment.id)} /></td>
        <td><CdpStatus environment={environment} /></td>
        <td><AuthStatus environment={environment} /></td>
        <td><EnvironmentActions environment={environment} disabled={submitting || environment.busy || pendingIds.has(environment.id)}
          onAction={onAction} onDetails={onDetails} onClose={onClose} /></td>
      </tr>)}</tbody>
    </table>
    {!environments.length ? <div className="table-empty"><SearchX size={26} /><strong>没有匹配的浏览器环境</strong><span>试试其他关键词或切换筛选条件。</span></div> : null}
  </div>;
}
