import { Archive, ArchiveRestore, Power, SearchX, Trash2 } from 'lucide-react';
import { formatTime, type Environment } from '../types';
import { CdpStatus, RunningStatus } from './EnvironmentStatus';

interface Props {
  environments: Environment[];
  pendingIds: Set<string>;
  submitting: boolean;
  onDetails: (id: string) => void;
  onRestore: (id: string) => void;
  onDelete: (id: string) => void;
  onClose: (ids: string[]) => void;
}

export default function ArchivedEnvironmentTable({ environments, pendingIds, submitting, onDetails, onRestore, onDelete, onClose }: Props) {
  return <div className="table-scroll" data-testid="archived-environment-list">
    <table className="browser-table archived-table"><thead><tr><th>已归档环境</th><th>运行状态</th><th>CDP</th><th>归档时间</th><th className="actions-heading">操作</th></tr></thead>
      <tbody>{environments.map(environment => {
        const disabled = submitting || environment.busy || pendingIds.has(environment.id);
        return <tr key={environment.id} data-testid={`archived-environment-${environment.id}`}>
          <td><button className="environment-name" onClick={() => onDetails(environment.id)}><span className="environment-avatar"><Archive size={19} strokeWidth={1.7} /></span>
            <span><strong>{environment.name}</strong><small>{environment.id}</small></span></button></td>
          <td><RunningStatus environment={environment} pending={pendingIds.has(environment.id)} /></td><td><CdpStatus environment={environment} /></td>
          <td className="archived-time">{environment.archived_at ? formatTime(environment.archived_at, true) : '已归档'}</td>
          <td><div className="row-actions"><button className="button button-small button-text" onClick={() => onDetails(environment.id)}>详情</button>
            {environment.running ? <button className="button button-small button-danger-quiet" disabled={disabled} onClick={() => onClose([environment.id])}
              data-testid={`close-archived-${environment.id}`}><Power size={13} />关闭</button> : null}
            <button className="button button-small button-outline" disabled={disabled} data-testid={`restore-environment-${environment.id}`}
              onClick={() => onRestore(environment.id)}><ArchiveRestore size={13} />恢复</button>
            <button className="button button-small button-danger-quiet" disabled={disabled || environment.running} title={environment.running ? '请先关闭环境' : undefined}
              data-testid={`delete-environment-${environment.id}`} onClick={() => onDelete(environment.id)}><Trash2 size={13} />删除登记</button>
          </div></td>
        </tr>;
      })}</tbody>
    </table>
    {!environments.length ? <div className="table-empty"><SearchX size={26} /><strong>暂无匹配的归档环境</strong><span>可在活动环境详情中归档已关闭的环境。</span></div> : null}
  </div>;
}
