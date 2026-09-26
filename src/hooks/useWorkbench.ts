import { useCallback, useEffect, useRef, useState } from 'react';
import { actionNames, type Action, type CreatedShop, type CreateShopInput, type Environment, type Job, type Maintenance, type MaintenanceSettings, type WorkbenchState } from '../types';

export interface Notice { tone: 'success' | 'warning'; message: string }
interface PendingJob { ids: string[]; action: Action }
const active = (status: string) => status === 'queued' || status === 'running';
const restartedMessage = '本机服务已重启，请先核对实际环境和操作状态，再决定是否重试。';

async function readResponse(response: Response): Promise<unknown> {
  let body: unknown;
  try { body = await response.json(); } catch { throw new Error(`服务返回了无法读取的响应（${response.status}）`); }
  if (!response.ok) {
    const value = body as { message?: string; error?: string | { code?: string; message?: string }; code?: string };
    const error = typeof value.error === 'object' ? value.error : null;
    const code = error?.code ?? value.code;
    if (code === 'profile_locked') throw new Error('这个环境正在被其他任务使用，暂时不能操作。请等待任务完成后重试。');
    if (code === 'maintenance_busy') throw new Error('已有登录态维护正在排队或执行，请等待这一轮完成。');
    if (code === 'close_all_busy') throw new Error('全部店铺的关闭操作已在排队或执行，请等待完成。');
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
  const [maintenanceSaving, setMaintenanceSaving] = useState(false);
  const [maintenanceSubmitting, setMaintenanceSubmitting] = useState(false);
  const [pendingMaintenanceId, setPendingMaintenanceId] = useState<string | null>(null);
  const [closeAllSubmitting, setCloseAllSubmitting] = useState(false);
  const [pendingCloseAllId, setPendingCloseAllId] = useState<string | null>(null);
  const stateRef = useRef<WorkbenchState | null>(null);
  const pendingRef = useRef<Record<string, PendingJob>>({});
  const postRequests = useRef(new Set<AbortController>());
  const posting = useRef(false);
  const maintenancePosting = useRef(false);
  const closeAllPosting = useRef(false);
  const closeAllPending = useRef<string | null>(null);
  const createPosting = useRef(false);
  const pickerPosting = useRef(false);
  const mounted = useRef(true);
  const refresh = useCallback(() => { setActionError(null); setRevision(value => value + 1); }, []);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; for (const request of postRequests.current) request.abort(); };
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | undefined;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      request = new AbortController();
      let timedOut = false;
      deadline = setTimeout(() => { timedOut = true; request?.abort(); }, 15000);
      setRefreshing(true);
      try {
        const response = await fetch('/api/state', { signal: request.signal, cache: 'no-store' });
        const next = await readResponse(response) as WorkbenchState;
        if (!Array.isArray(next.environments) || !Array.isArray(next.jobs) || typeof next.csrf_token !== 'string') {
          throw new Error('服务状态不完整，请刷新重试。');
        }
        if (stopped) return;
        const serverChanged = stateRef.current !== null && stateRef.current.csrf_token !== next.csrf_token;
        stateRef.current = next;
        setState(next);
        setError(null);
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
        if (!stopped && (timedOut || !(reason instanceof DOMException && reason.name === 'AbortError'))) {
          setError(timedOut ? '读取本机状态超时，请确认服务仍在运行。' : reason instanceof Error ? reason.message : '暂时无法连接本地服务。');
        }
      } finally {
        clearTimeout(deadline);
        if (!stopped) {
          setRefreshing(false);
          const busy = Object.keys(pendingRef.current).length > 0 || posting.current || stateRef.current?.jobs.some(job => active(job.status));
          timer = setTimeout(poll, busy ? 1000 : 5000);
        }
      }
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); clearTimeout(deadline); request?.abort(); };
  }, [revision]);

  const runAction = useCallback(async (action: Action, ids: string[]): Promise<boolean> => {
    const current = stateRef.current;
    if (!current || !ids.length || posting.current) return false;
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
    if (!current || maintenancePosting.current) return false;
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

  const closeAllShops = useCallback(async (pauseMaintenance: boolean): Promise<boolean> => {
    if (closeAllPosting.current || closeAllPending.current) return false;
    closeAllPosting.current = true;
    setCloseAllSubmitting(true);
    setActionError(null);
    setNotice(null);
    try {
      const result = await postShopRequest('/api/shops/close-all', { pause_maintenance: pauseMaintenance }) as { job: Pick<Job, 'id' | 'status'> & Partial<Job> };
      if (!result.job?.id) throw new Error('后台未返回关闭作业编号，请刷新状态核对。');
      if (!mounted.current) return false;
      const ids = result.job.ids ?? stateRef.current?.environments.filter(environment => environment.kind === 'shop').map(environment => environment.id) ?? [];
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
    if (createPosting.current) throw new Error('正在创建店铺，请等待完成。');
    const requestToken = stateRef.current?.csrf_token;
    createPosting.current = true;
    try {
      const result = await postShopRequest('/api/shops', input) as { environment: CreatedShop & Partial<Environment> };
      if (!result.environment?.id) throw new Error('后台未返回新店铺信息，请刷新列表核对后再试。');
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
        message: `店铺“${created.name}”已创建${openAfter ? started ? '，打开主页已排队。' : '；打开主页未提交，可在列表中点击“打开”。' : '，可在列表中点击“打开”。'}` });
      return created;
    } finally { createPosting.current = false; }
  }, [postShopRequest, refresh, runAction]);

  const pendingIds = new Set([...Object.values(pendingJobs).flatMap(job => job.ids), ...submittingIds]);
  const pendingStartJobs = Object.values(pendingJobs).filter(job => job.action === 'start');
  return { state, error: actionError ?? error, notice, refreshing, pendingIds, pendingStartJobs, submitting: submittingIds.length > 0,
    refresh, runAction, dismissNotice: () => setNotice(null), saveMaintenance, runMaintenance, maintenanceSaving,
    closeAllShops, closeAllSubmitting, closeAllPending: closeAllSubmitting || pendingCloseAllId !== null || !!state?.jobs.some(job => job.close_all && active(job.status)),
    createShop, pickFolder,
    maintenanceStarting: maintenanceSubmitting || pendingMaintenanceId !== null || !!state?.jobs.some(job => job.action === 'maintain-session' && active(job.status)) };
}
