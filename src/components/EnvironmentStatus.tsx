import { LoaderCircle } from 'lucide-react';
import type { Environment } from '../types';

const authLabels = { verified: '已登录', required: '需登录', unchecked: '未检查', error: '检查失败' };
const cdpLabels = { connected: '已连接', unavailable: '未响应', stopped: '未启动', conflict: '端口冲突' };

export function RunningStatus({ environment, pending = false }: { environment: Environment; pending?: boolean }) {
  if (environment.busy || pending) return <span className="status status-blue"><LoaderCircle className="spin" size={12} />处理中</span>;
  return <span className={`status ${environment.running ? 'status-green' : 'status-neutral'}`}><i />{environment.running ? '运行中' : '已关闭'}</span>;
}

export function AuthStatus({ environment }: { environment: Environment }) {
  const status = environment.auth.status;
  return <span title={environment.auth.message || undefined} className={`auth-state auth-${status}`}>
    <i />{authLabels[status] ?? '未检查'}
  </span>;
}

export function CdpStatus({ environment }: { environment: Environment }) {
  return <span className={`cdp-state cdp-${environment.cdp}`}><i />{cdpLabels[environment.cdp] ?? '未知'}</span>;
}
