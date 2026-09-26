export type Action = 'start' | 'focus' | 'check-login' | 'save-session' | 'maintain-session' | 'close' | 'open-folder' | 'open-results';
export type AuthStatus = 'verified' | 'required' | 'unchecked' | 'error';
export type JobStatus = 'queued' | 'running' | 'complete' | 'partial' | 'failed';

export interface Environment {
  id: string;
  name: string;
  kind: 'shop' | 'shared';
  login_username?: string;
  running: boolean;
  cdp: 'connected' | 'unavailable' | 'stopped' | 'conflict';
  auth: { status: AuthStatus; checked_at?: string | null; message?: string; identity?: string | Record<string, unknown> | null };
  cookie_sync?: {
    status: 'idle' | 'saved' | 'partial' | 'error';
    last_saved_at: string | null;
    persisted_count: number;
    failed_count: number;
    skipped_count: number;
    message: string;
  };
  busy: boolean;
  tabs_count: number | null;
  user_data_dir: string;
  profile_directory: string;
  config_path: string;
  download_dir: string;
  executable_path?: string | null;
  debug_port: number;
  cdp_endpoint: string;
  current_site?: string | null;
  current_url?: string | null;
  configured_urls?: Record<string, string>;
  tabs?: { title?: string; url: string }[];
}

export interface Job {
  id: string;
  action: Action;
  ids: string[];
  status: JobStatus;
  results?: { id: string; status: string; code?: string; message?: string }[];
  created_at?: string;
  close_all?: boolean;
}

export interface CreateShopInput {
  name: string;
  parent_folder: string;
  login_username: string;
}

export interface CreatedShop {
  id: string;
  name: string;
  user_data_dir: string;
  debug_port: number;
  config_path: string;
  folder?: string;
}

export interface Activity {
  id?: string;
  at: string;
  environment_id: string;
  name: string;
  action: string;
  level: 'error' | 'info';
  message: string;
}

export interface MaintenanceSettings {
  enabled: boolean;
  interval_minutes: number;
  include_shared: boolean;
}

export interface Maintenance extends MaintenanceSettings {
  next_run_at: string | null;
  running: boolean;
  last_run_at: string | null;
  last_finished_at: string | null;
  last_status: 'idle' | 'running' | 'complete' | 'partial' | 'failed' | 'paused';
  last_results: { id: string; name: string; status: 'complete' | 'skipped' | 'failed'; message: string; code?: string }[];
  message: string;
}

export interface WorkbenchState {
  csrf_token: string;
  environments: Environment[];
  registry_path: string;
  issuer: string;
  updated_at: string;
  activity: Activity[];
  jobs: Job[];
  inventory_error?: string | null;
  error?: { code: string; message: string } | null;
  maintenance?: Maintenance;
  creation_defaults?: { parent_folder: string };
}

export const actionNames: Record<Action, string> = {
  start: '打开环境', focus: '定位窗口', 'check-login': '检查登录', close: '关闭环境',
  'save-session': '保存登录态',
  'maintain-session': '登录态维护',
  'open-folder': '打开环境目录', 'open-results': '打开结果目录',
};

export function formatTime(value?: string | null, full = false): string {
  if (!value) return '尚未检查';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间不可用';
  return new Intl.DateTimeFormat('zh-CN', full ? {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  } : { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(date);
}
