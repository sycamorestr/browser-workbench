import { useCallback, useEffect, useRef, useState } from 'react';
import { actionNames, type Action, type CreatedShop, type CreateShopInput, type Environment, type Job, type LoginCheckSettings, type Maintenance, type MaintenanceSettings, type RegistrationAction, type RegistrationState, type ShutdownStatus, type WorkbenchState } from '../types';

export interface Notice { tone: 'success' | 'warning'; message: string }
interface PendingJob { ids: string[]; action: Action }
const active = (status: string) => status === 'queued' || status === 'running';
const restartedMessage = '本机服务已重启，请先核对实际环境和操作状态，再决定是否重试。';

function applyRegistration(state: WorkbenchState, id: string, target: RegistrationState, environment: Environment): WorkbenchState {
  const environments = state.environments.filter(item => item.id !== id);
  const archived = (state.archived_environments ?? []).filter(item => item.id !== id);
  if (target === 'active') environments.push({ ...environment, archived_at: null });
  if (target === 'archived') archived.push({ ...environment, archived_at: environment.archived_at ?? new Date().toISOString() });
  return { ...state, environments, archived_environments: archived };
}

async function readResponse(response: Response): Promise<unknown> {
  let body: unknown;
  try { body = await response.json(); } catch { throw new Error(`服务返回了无法读取的响应（${response.status}）`); }
  if (!response.ok) {
    const value = body as { message?: string; error?: string | { code?: string; message?: string }; code?: string };
    const error = typeof value.error === 'object' ? value.error : null;
    const code = error?.code ?? value.code;
    if (code === 'profile_locked') throw new Error('这个环境正在被其他任务使用，暂时不能操作。请等待任务完成后重试。');
    if (code === 'maintenance_busy') throw new Error('已有登录态维护正在排队或执行，请等待这一轮完成。');
    if (code === 'close_all_busy') throw new Error('全部环境的关闭操作已在排队或执行，请等待完成。');
    throw new Error(error?.message ?? (typeof value.error === 'string' ? value.error : value.message) ?? `请求失败（${response.status}）`);
  }
  return body;
}

export function useWorkbench() {
  const [state, setState] = useState<WorkbenchState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [revision, setRevision] = useState(0);
  const [pendingJobs, setPendingJobs] = useState<Record<string, PendingJob>>({});
  const [submittingIds, setSubmittingIds] = useState<string[]>([]);
  const [loginCheckSavingIds, setLoginCheckSavingIds] = useState<string[]>([]);
  const [registrationIds, setRegistrationIds] = useState<string[]>([]);
  const [maintenanceSaving, setMaintenanceSaving] = useState(false);
  const [maintenanceSubmitting, setMaintenanceSubmitting] = useState(false);
  const [pendingMaintenanceId, setPendingMaintenanceId] = useState<string | null>(null);
  const [closeAllSubmitting, setCloseAllSubmitting] = useState(false);
  const [pendingCloseAllId, setPendingCloseAllId] = useState<string | null>(null);
  const [shutdownStatus, setShutdownStatus] = useState<ShutdownStatus>('running');
  const [shutdownError, setShutdownError] = useState<string | null>(null);
  const [shutdownMessage, setShutdownMessage] = useState<string | null>(null);
  const shutdownStatusRef = useRef<ShutdownStatus>('running');
  const shutdownPosting = useRef(false);
  const stateRef = useRef<WorkbenchState | null>(null);
  const pendingRef = useRef<Record<string, PendingJob>>({});
  const postRequests = useRef(new Set<AbortController>());
  const posting = useRef(false);
  const maintenancePosting = useRef(false);
  const closeAllPosting = useRef(false);
  const closeAllPending = useRef<string | null>(null);
  const createPosting = useRef(false);
  const pickerPosting = useRef(false);
  const loginCheckPosting = useRef(new Set<string>());
  const stateMutationVersion = useRef(0);
  const loginCheckUpdates = useRef(new Map<string, { version: number; login_check: LoginCheckSettings; auth: Environment['auth'] }>());
  const registrationPosting = useRef(new Set<string>());
  const registrationUpdates = useRef(new Map<string, { version: number; state: RegistrationState; environment: Environment }>());
  const mounted = useRef(true);
  const refresh = useCallback(() => {
    if (shutdownStatusRef.current !== 'running') return;
    setActionError(null);
    setRevision(value => value + 1);
  }, []);
  const updateShutdownStatus = useCallback((value: ShutdownStatus) => {
    shutdownStatusRef.current = value;
    setShutdownStatus(value);
  }, []);
  const enterStopping = useCallback(() => {
    updateShutdownStatus('stopping');
    setShutdownError(null);
    setShutdownMessage(null);
    setActionError(null);
    setError(null);
    setRefreshing(false);
  }, [updateShutdownStatus]);
  const monitoringShutdown = shutdownStatus === 'stopping' || shutdownStatus === 'stopped';

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; for (const request of postRequests.current) request.abort(); };
  }, []);

  useEffect(() => {
    if (monitoringShutdown) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | undefined;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      const settingsVersion = stateMutationVersion.current;
      request = new AbortController();
      let timedOut = false;
      deadline = setTimeout(() => { timedOut = true; request?.abort(); }, 15000);
      setRefreshing(true);
      try {
        const response = await fetch('/api/state', { signal: request.signal, cache: 'no-store' });
        let next = await readResponse(response) as WorkbenchState;
        if (!Array.isArray(next.environments) || !Array.isArray(next.jobs) || typeof next.csrf_token !== 'string'
            || (next.archived_environments !== undefined && !Array.isArray(next.archived_environments))) {
          throw new Error('服务状态不完整，请刷新重试。');
        }
        if (stopped) return;
        const serverChanged = stateRef.current !== null && stateRef.current.csrf_token !== next.csrf_token;
        if (serverChanged) { loginCheckUpdates.current.clear(); registrationUpdates.current.clear(); }
        else next.environments = next.environments.map(environment => {
          const saved = loginCheckUpdates.current.get(environment.id);
          return saved && saved.version > settingsVersion ? { ...environment, login_check: saved.login_check, auth: saved.auth } : environment;
        });
        if (!serverChanged) for (const [id, update] of registrationUpdates.current) {
          if (update.version > settingsVersion) next = applyRegistration(next, id, update.state, update.environment);
        }
        stateRef.current = next;
        setState(next);
        setError(null);
        if (next.service_status === 'stopping') enterStopping();
        if (serverChanged) {
          for (const pendingRequest of postRequests.current) pendingRequest.abort();
          pendingRef.current = {};
          setPendingJobs({});
          setPendingMaintenanceId(null);
          closeAllPending.current = null;
          setPendingCloseAllId(null);
          setActionError(null);
          setNotice({ tone: 'warning', message: restartedMessage });
        }
        const pending = { ...pendingRef.current };
        let changed = false;
        for (const job of next.jobs) {
          if (pending[job.id] && !active(job.status)) {
            delete pending[job.id];
            changed = true;
            setPendingMaintenanceId(current => current === job.id ? null : current);
            if (closeAllPending.current === job.id) {
              closeAllPending.current = null;
              setPendingCloseAllId(null);
            }
            setNotice({ tone: job.status === 'complete' ? 'success' : 'warning',
              message: `${actionNames[job.action] ?? '操作'}${job.status === 'complete' ? '已完成' : '有环境需要处理，请查看最近操作'}` });
          }
        }
        if (changed) { pendingRef.current = pending; setPendingJobs(pending); }
      } catch (reason) {
        if (!stopped && shutdownStatusRef.current !== 'stopping' && shutdownStatusRef.current !== 'stopped' && (timedOut || !(reason instanceof DOMException && reason.name === 'AbortError'))) {
          setError(timedOut ? '读取本机状态超时，请确认服务仍在运行。' : reason instanceof Error ? reason.message : '暂时无法连接本地服务。');
        }
      } finally {
        clearTimeout(deadline);
        if (!stopped && shutdownStatusRef.current !== 'stopping' && shutdownStatusRef.current !== 'stopped') {
          setRefreshing(false);
          const busy = Object.keys(pendingRef.current).length > 0 || posting.current || stateRef.current?.jobs.some(job => active(job.status));
          timer = setTimeout(poll, busy ? 1000 : 5000);
        }
      }
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); clearTimeout(deadline); request?.abort(); };
  }, [revision, monitoringShutdown, enterStopping]);

  useEffect(() => {
    if (!monitoringShutdown) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | undefined;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    let connectionFailures = 0;
    const pollHealth = async () => {
      request = new AbortController();
      let timedOut = false;
      let receivedResponse = false;
      deadline = setTimeout(() => { timedOut = true; request?.abort(); }, 5000);
      try {
        const response = await fetch('/api/health', { signal: request.signal, cache: 'no-store' });
        receivedResponse = true;
        const health = await readResponse(response) as { service_status?: string };
        if (disposed) return;
        connectionFailures = 0;
        if (health.service_status === 'stopping') {
          updateShutdownStatus('stopping');
          setShutdownMessage(null);
        } else if (health.service_status === 'running') {
          // Obtain a fresh state and CSRF token before accepting any new actions.
          for (const pendingRequest of postRequests.current) pendingRequest.abort();
          stateRef.current = null;
          loginCheckUpdates.current.clear();
          registrationUpdates.current.clear();
          setState(null);
          pendingRef.current = {};
          setPendingJobs({});
          setPendingMaintenanceId(null);
          closeAllPending.current = null;
          setPendingCloseAllId(null);
          setShutdownError(null);
          setShutdownMessage(null);
          setError(null);
          setActionError(null);
          setNotice({ tone: 'warning', message: restartedMessage });
          updateShutdownStatus('running');
        } else {
          setShutdownMessage('服务仍有响应，但暂时无法确认停止状态，正在继续检查。');
        }
      } catch (reason) {
        if (disposed) return;
        if (timedOut) {
          connectionFailures = 0;
          setShutdownMessage('确认停止状态超时，正在继续检查；尚未确认后台已停止。');
        } else if (!receivedResponse && reason instanceof TypeError) {
          // An aborted or slow request is not evidence that the server exited.
          connectionFailures += 1;
          if (connectionFailures >= 2) {
            updateShutdownStatus('stopped');
            setShutdownMessage(null);
          }
        } else if (!(reason instanceof DOMException && reason.name === 'AbortError')) {
          connectionFailures = 0;
          setShutdownMessage('暂时无法确认停止状态，正在继续检查。');
        }
      } finally {
        clearTimeout(deadline);
        if (!disposed && shutdownStatusRef.current !== 'running') {
          timer = setTimeout(pollHealth, shutdownStatusRef.current === 'stopped' ? 5000 : 1000);
        }
      }
    };
    void pollHealth();
    return () => { disposed = true; clearTimeout(timer); clearTimeout(deadline); request?.abort(); };
  }, [monitoringShutdown, updateShutdownStatus]);

  const stopWorkbench = useCallback(async (): Promise<boolean> => {
    const current = stateRef.current;
    if (shutdownPosting.current || shutdownStatusRef.current !== 'running') return false;
    if (!current) {
      setShutdownError('尚未连接本机服务，请等待连接恢复后重试。');
      return false;
    }
    shutdownPosting.current = true;
    updateShutdownStatus('requesting');
    setShutdownError(null);
    setShutdownMessage(null);
    const request = new AbortController();
    let timedOut = false;
    const deadline = setTimeout(() => { timedOut = true; request.abort(); }, 15000);
    postRequests.current.add(request);
    try {
      const response = await fetch('/api/shutdown', {
        method: 'POST', signal: request.signal,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': current.csrf_token },
        body: JSON.stringify({}),
      });
      const result = await readResponse(response) as { status?: string };
      if (stateRef.current?.csrf_token !== current.csrf_token) throw new Error(restartedMessage);
      if (response.status !== 202 || result.status !== 'stopping') throw new Error('后台未确认停止请求，请核对状态后重试。');
      if (!mounted.current) return false;
      enterStopping();
      return true;
    } catch (reason) {
      if (!mounted.current) return false;
      // Another state poll may have independently confirmed acceptance.
      if (['stopping', 'stopped'].includes(shutdownStatusRef.current)) return true;
      updateShutdownStatus('running');
      setShutdownError(timedOut ? '停止请求超时，尚未确认后台是否接受，请核对状态后重试。' : reason instanceof Error ? `停止请求未确认：${reason.message}` : '停止请求未确认，请核对状态后重试。');
      return false;
    } finally {
      clearTimeout(deadline);
      postRequests.current.delete(request);
      shutdownPosting.current = false;
    }
  }, [enterStopping, updateShutdownStatus]);

  const runAction = useCallback(async (action: Action, ids: string[]): Promise<boolean> => {
    const current = stateRef.current;
    if (shutdownStatusRef.current !== 'running' || !current || !ids.length || posting.current
        || ids.some(id => loginCheckPosting.current.has(id) || registrationPosting.current.has(id))) return false;
    const archivedIds = new Set((current.archived_environments ?? []).map(environment => environment.id));
    if (ids.some(id => archivedIds.has(id)) && !['close', 'open-folder', 'open-results'].includes(action)) {
      setActionError('请先恢复归档环境，再执行此操作。');
      return false;
    }
    posting.current = true;
    setSubmittingIds(ids);
    setActionError(null);
    setNotice(null);
    const request = new AbortController();
    let timedOut = false;
    const deadline = setTimeout(() => { timedOut = true; request.abort(); }, 15000);
    postRequests.current.add(request);
    try {
      const response = await fetch('/api/actions', {
        method: 'POST', signal: request.signal,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': current.csrf_token },
        body: JSON.stringify({ action, environment_ids: ids }),
      });
      const result = await readResponse(response) as Job | { job: Job };
      if (stateRef.current?.csrf_token !== current.csrf_token) throw new Error(restartedMessage);
      const job = 'job' in result ? result.job : result;
      if (!job?.id) throw new Error('服务未返回操作编号，请检查最近操作后重试。');
      if (!mounted.current) return false;
      const next = { ...pendingRef.current, [job.id]: { ids, action } };
      pendingRef.current = next;
      setPendingJobs(next);
      refresh();
      return true;
    } catch (reason) {
      if (mounted.current && (timedOut || !(reason instanceof DOMException && reason.name === 'AbortError'))) {
        setActionError(timedOut ? '提交操作超时，请先刷新状态并核对最近操作，再决定是否重试。' : reason instanceof Error ? reason.message : '操作未提交，请重试。');
      }
      return false;
    } finally {
      clearTimeout(deadline);
      postRequests.current.delete(request);
      posting.current = false;
      if (mounted.current) setSubmittingIds([]);
    }
  }, [refresh]);

  const maintenanceRequest = useCallback(async (settings?: MaintenanceSettings): Promise<boolean> => {
    const current = stateRef.current;
    if (shutdownStatusRef.current !== 'running' || !current || maintenancePosting.current) return false;
    maintenancePosting.current = true;
    const savingSettings = settings !== undefined;
    if (savingSettings) setMaintenanceSaving(true); else setMaintenanceSubmitting(true);
    setActionError(null);
    setNotice(null);
    const request = new AbortController();
    let timedOut = false;
    const deadline = setTimeout(() => { timedOut = true; request.abort(); }, 15000);
    postRequests.current.add(request);
    try {
      const response = await fetch(savingSettings ? '/api/maintenance' : '/api/maintenance/run', {
        method: 'POST', signal: request.signal,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': current.csrf_token },
        body: JSON.stringify(settings ?? {}),
      });
      const result = await readResponse(response) as { maintenance?: Maintenance; job?: Pick<Job, 'id' | 'status'> & Partial<Job> };
      if (stateRef.current?.csrf_token !== current.csrf_token) throw new Error(restartedMessage);
      if (!mounted.current) return false;
      if (savingSettings) {
        if (!result.maintenance) throw new Error('后台未返回维护设置，请刷新状态核对。');
        const next = { ...stateRef.current!, maintenance: result.maintenance };
        stateRef.current = next;
        setState(next);
        setNotice({ tone: 'success', message: settings.enabled ? '定时维护设置已保存' : '维护设置已保存，定时维护已暂停' });
      } else {
        if (!result.job?.id) throw new Error('后台未返回维护作业编号，请刷新状态核对。');
        const pending = { ...pendingRef.current, [result.job.id]: { ids: result.job.ids ?? [], action: 'maintain-session' as const } };
        pendingRef.current = pending;
        setPendingJobs(pending);
        setPendingMaintenanceId(result.job.id);
      }
      refresh();
      return true;
    } catch (reason) {
      if (mounted.current && (timedOut || !(reason instanceof DOMException && reason.name === 'AbortError'))) {
        setActionError(timedOut ? '维护请求超时，请刷新状态核对后再重试。' : reason instanceof Error ? reason.message : '维护请求未完成。');
      }
      return false;
    } finally {
      clearTimeout(deadline);
      postRequests.current.delete(request);
      maintenancePosting.current = false;
      if (mounted.current) { setMaintenanceSaving(false); setMaintenanceSubmitting(false); }
    }
  }, [refresh]);

  const saveMaintenance = useCallback((settings: MaintenanceSettings) => maintenanceRequest(settings), [maintenanceRequest]);
  const runMaintenance = useCallback(() => maintenanceRequest(), [maintenanceRequest]);

  const postShopRequest = useCallback(async (path: string, body: unknown, timeout = 15000, signal?: AbortSignal): Promise<unknown> => {
    if (shutdownStatusRef.current !== 'running') throw new Error('工作台正在停止，暂时不能执行其他操作。');
    const current = stateRef.current;
    if (!current) throw new Error('尚未连接本机服务，请稍后再试。');
    const request = new AbortController();
    const abort = () => request.abort();
    if (signal?.aborted) request.abort();
    signal?.addEventListener('abort', abort, { once: true });
    let timedOut = false;
    const deadline = setTimeout(() => { timedOut = true; request.abort(); }, timeout);
    postRequests.current.add(request);
    try {
      const result = await readResponse(await fetch(path, { method: 'POST', signal: request.signal,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': current.csrf_token }, body: JSON.stringify(body) }));
      if (stateRef.current?.csrf_token !== current.csrf_token) throw new Error(restartedMessage);
      return result;
    } catch (reason) {
      if (stateRef.current?.csrf_token !== current.csrf_token) throw new Error(restartedMessage);
      if (timedOut) throw new Error(path === '/api/folders/pick' ? '选择文件夹等待超时，可以重新选择或直接填写路径。' : '请求超时，请先刷新列表确认结果，再决定是否重试。');
      throw reason;
    } finally {
      clearTimeout(deadline);
      signal?.removeEventListener('abort', abort);
      postRequests.current.delete(request);
    }
  }, []);

  const saveLoginCheck = useCallback(async (environmentId: string, settings: LoginCheckSettings): Promise<LoginCheckSettings> => {
    if (loginCheckPosting.current.has(environmentId) || registrationPosting.current.has(environmentId)) throw new Error('该环境正在更新设置，请稍候。');
    const environment = stateRef.current?.environments.find(item => item.id === environmentId);
    if (stateRef.current?.archived_environments?.some(item => item.id === environmentId)) throw new Error('该环境已归档，请先恢复后修改登录检查设置。');
    if (!environment) throw new Error('环境信息尚未读取，请刷新后重试。');
    if (environment.busy || posting.current || Object.values(pendingRef.current).some(job => job.ids.includes(environmentId))) {
      throw new Error('该环境正在执行操作，请等待完成后保存。');
    }
    loginCheckPosting.current.add(environmentId);
    setLoginCheckSavingIds([...loginCheckPosting.current]);
    try {
      const result = await postShopRequest('/api/environments/login-check', { environment_id: environmentId, ...settings }) as {
        environment_id: string; login_check: LoginCheckSettings; auth: Environment['auth'];
      };
      if (result.environment_id !== environmentId || !result.login_check || !['url', 'platform'].includes(result.login_check.mode)
          || typeof result.login_check.login_url !== 'string' || !Number.isInteger(result.login_check.wait_seconds)
          || result.login_check.wait_seconds < 1 || result.login_check.wait_seconds > 30
          || !result.auth || !['verified', 'assumed', 'required', 'unchecked', 'error'].includes(result.auth.status)) {
        throw new Error('后台未返回完整的登录检查设置，请刷新状态核对后重试。');
      }
      if (mounted.current && stateRef.current) {
        loginCheckUpdates.current.set(environmentId, { version: ++stateMutationVersion.current, login_check: result.login_check, auth: result.auth });
        const next = { ...stateRef.current, environments: stateRef.current.environments.map(item => item.id === environmentId
          ? { ...item, login_check: result.login_check, auth: result.auth } : item) };
        stateRef.current = next;
        setState(next);
        refresh();
      }
      return result.login_check;
    } finally {
      loginCheckPosting.current.delete(environmentId);
      if (mounted.current) setLoginCheckSavingIds([...loginCheckPosting.current]);
    }
  }, [postShopRequest, refresh]);

  const changeRegistration = useCallback(async (action: RegistrationAction, environmentId: string, confirmName?: string): Promise<boolean> => {
    if (shutdownStatusRef.current !== 'running' || registrationPosting.current.has(environmentId)) return false;
    setActionError(null);
    setNotice(null);
    const current = stateRef.current;
    const source = (action === 'archive' ? current?.environments : current?.archived_environments)?.find(item => item.id === environmentId);
    if (!source) { setActionError('环境状态已变化，请刷新列表后重试。'); return false; }
    if (source.busy || posting.current || loginCheckPosting.current.has(environmentId)
        || Object.values(pendingRef.current).some(job => job.ids.includes(environmentId))) {
      setActionError('该环境正在执行操作，请等待完成后重试。');
      return false;
    }
    if (source.running && action !== 'restore') { setActionError('请先关闭该环境，再归档或删除登记。'); return false; }
    if (action === 'delete' && confirmName !== source.name) { setActionError('请输入完整且一致的环境名称以确认删除登记。'); return false; }
    registrationPosting.current.add(environmentId);
    setRegistrationIds([...registrationPosting.current]);
    try {
      const result = await postShopRequest(`/api/environments/${action}`, {
        environment_id: environmentId, ...(action === 'delete' ? { confirm_name: confirmName } : {}),
      }) as { environment_id: string; state: RegistrationState };
      const expected: RegistrationState = action === 'archive' ? 'archived' : action === 'restore' ? 'active' : 'deleted';
      if (result.environment_id !== environmentId || result.state !== expected) throw new Error('后台未确认环境登记状态，请刷新列表核对后重试。');
      if (!mounted.current || !stateRef.current) return false;
      const latest = [...stateRef.current.environments, ...(stateRef.current.archived_environments ?? [])].find(item => item.id === environmentId) ?? source;
      const environment = expected === 'archived' ? { ...latest, archived_at: new Date().toISOString() } : latest;
      registrationUpdates.current.set(environmentId, { version: ++stateMutationVersion.current, state: expected, environment });
      loginCheckUpdates.current.delete(environmentId);
      const next = applyRegistration(stateRef.current, environmentId, expected, environment);
      stateRef.current = next;
      setState(next);
      setNotice({ tone: 'success', message: action === 'archive' ? `“${source.name}”已归档，本地登录与下载数据已保留。`
        : action === 'restore' ? `“${source.name}”已恢复到活动环境。` : `“${source.name}”已从工作台移除，本地浏览器与下载文件已保留。` });
      refresh();
      return true;
    } catch (reason) {
      if (mounted.current) setActionError(reason instanceof Error ? reason.message : '环境登记未更新，请重试。');
      return false;
    } finally {
      registrationPosting.current.delete(environmentId);
      if (mounted.current) setRegistrationIds([...registrationPosting.current]);
    }
  }, [postShopRequest, refresh]);

  const closeAllShops = useCallback(async (pauseMaintenance: boolean): Promise<boolean> => {
    if (shutdownStatusRef.current !== 'running' || closeAllPosting.current || closeAllPending.current) return false;
    closeAllPosting.current = true;
    setCloseAllSubmitting(true);
    setActionError(null);
    setNotice(null);
    try {
      const result = await postShopRequest('/api/environments/close-all', { pause_maintenance: pauseMaintenance }) as { job: Pick<Job, 'id' | 'status'> & Partial<Job> };
      if (!result.job?.id) throw new Error('后台未返回关闭作业编号，请刷新状态核对。');
      if (!mounted.current) return false;
      const ids = result.job.ids ?? stateRef.current?.environments.map(environment => environment.id) ?? [];
      const pending = { ...pendingRef.current, [result.job.id]: { ids, action: 'close' as const } };
      pendingRef.current = pending;
      setPendingJobs(pending);
      closeAllPending.current = result.job.id;
      setPendingCloseAllId(result.job.id);
      refresh();
      return true;
    } catch (reason) {
      if (mounted.current) setActionError(reason instanceof Error ? reason.message : '关闭请求未完成，请重试。');
      return false;
    } finally {
      closeAllPosting.current = false;
      if (mounted.current) setCloseAllSubmitting(false);
    }
  }, [postShopRequest, refresh]);

  const pickFolder = useCallback(async (signal: AbortSignal): Promise<string | null> => {
    if (shutdownStatusRef.current !== 'running') throw new Error('工作台正在停止，暂时不能选择文件夹。');
    if (pickerPosting.current) throw new Error('已有文件夹选择窗口，请先完成或取消该窗口。');
    pickerPosting.current = true;
    try {
      const result = await postShopRequest('/api/folders/pick', {}, 180000, signal) as { path: string | null; cancelled: boolean };
      if (result.cancelled || result.path === null) return null;
      if (typeof result.path !== 'string') throw new Error('后台未返回有效文件夹路径，请手动填写。');
      return result.path;
    } finally { pickerPosting.current = false; }
  }, [postShopRequest]);

  const createShop = useCallback(async (input: CreateShopInput, openAfter: boolean): Promise<CreatedShop> => {
    if (shutdownStatusRef.current !== 'running') throw new Error('工作台正在停止，暂时不能新增环境。');
    if (createPosting.current) throw new Error('正在创建环境，请等待完成。');
    const requestToken = stateRef.current?.csrf_token;
    createPosting.current = true;
    try {
      const result = await postShopRequest('/api/environments', input) as { environment: CreatedShop & Partial<Environment> };
      if (!result.environment?.id) throw new Error('后台未返回新环境信息，请刷新列表核对后再试。');
      const created = result.environment;
      if (!mounted.current) return created;
      if (created.kind && created.auth && typeof created.running === 'boolean' && stateRef.current) {
        const next = { ...stateRef.current, environments: [...stateRef.current.environments.filter(environment => environment.id !== created.id), created as Environment] };
        stateRef.current = next;
        setState(next);
      }
      refresh();
      const started = openAfter ? await runAction('start', [created.id]) : false;
      if (mounted.current && stateRef.current?.csrf_token === requestToken) setNotice({ tone: openAfter && !started ? 'warning' : 'success',
        message: `环境“${created.name}”已创建${openAfter ? started ? '，打开页面已排队。' : '；打开页面未提交，可在列表中点击“打开”。' : '，可在列表中点击“打开”。'}` });
      return created;
    } finally { createPosting.current = false; }
  }, [postShopRequest, refresh, runAction]);

  const pendingIds = new Set([...Object.values(pendingJobs).flatMap(job => job.ids), ...submittingIds, ...loginCheckSavingIds, ...registrationIds]);
  const pendingStartJobs = Object.values(pendingJobs).filter(job => job.action === 'start');
  return { state, error: actionError ?? error, notice, refreshing, pendingIds, pendingStartJobs, submitting: submittingIds.length > 0,
    refresh, runAction, dismissNotice: () => setNotice(null), saveMaintenance, runMaintenance, maintenanceSaving,
    closeAllShops, closeAllSubmitting, closeAllPending: closeAllSubmitting || pendingCloseAllId !== null || !!state?.jobs.some(job => job.close_all && active(job.status)),
    createShop, pickFolder, saveLoginCheck, changeRegistration, registrationIds, stopWorkbench, shutdownStatus, shutdownError, shutdownMessage,
    maintenanceStarting: maintenanceSubmitting || pendingMaintenanceId !== null || !!state?.jobs.some(job => job.action === 'maintain-session' && active(job.status)) };
}
