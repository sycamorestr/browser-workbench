import { useEffect, useRef, useState, type FormEvent } from 'react';
import { LoaderCircle, Save, ShieldCheck } from 'lucide-react';
import type { Environment, LoginCheckSettings } from '../types';

interface Props {
  environment: Environment;
  pending: boolean;
  onSave: (environmentId: string, settings: LoginCheckSettings) => Promise<LoginCheckSettings>;
  onCheck: () => void;
}

function validateLoginUrl(value: string): string | null {
  const address = value.trim();
  if (/[\u0000-\u001f\u007f-\u009f]/.test(value) || /\s/.test(address) || address.includes('\\')) {
    return '登录页地址不能包含空白、控制字符或反斜杠。';
  }
  if (!address) return null;
  if (address.length > 4096) return '登录页地址不能超过 4096 个字符。';
  const authority = /^https?:\/\/([^/?#]+)/i.exec(address)?.[1];
  if (!authority) return '请填写以 http:// 或 https:// 开头、包含主机名的完整登录页地址，或留空使用通用识别。';
  try {
    const parsed = new URL(address);
    if (!parsed.hostname || !['http:', 'https:'].includes(parsed.protocol) || parsed.port === '0') {
      return '请填写有效的 HTTP 或 HTTPS 登录页地址。';
    }
    if (authority.includes('@') || parsed.username || parsed.password) return '登录页地址不能包含账号或密码。';
  } catch {
    return '登录页地址格式不正确，请检查主机名和端口。';
  }
  return null;
}

export default function LoginCheckPanel({ environment, pending, onSave, onCheck }: Props) {
  const savedMode = environment.login_check?.mode ?? 'url';
  const savedUrl = environment.login_check?.login_url ?? '';
  const savedWait = environment.login_check?.wait_seconds ?? 5;
  const [mode, setMode] = useState<LoginCheckSettings['mode']>(savedMode);
  const [loginUrl, setLoginUrl] = useState(savedUrl);
  const [waitSeconds, setWaitSeconds] = useState(String(savedWait));
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ error: boolean; message: string } | null>(null);
  const posting = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    if (editing) return;
    setMode(savedMode);
    setLoginUrl(savedUrl);
    setWaitSeconds(String(savedWait));
  }, [editing, savedMode, savedUrl, savedWait]);
  const dirty = mode !== savedMode || loginUrl.trim() !== savedUrl || Number(waitSeconds) !== savedWait || !waitSeconds.trim();
  const disabled = pending || saving;
  const edit = () => { setEditing(true); setFeedback(null); };
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (disabled || posting.current || !dirty) return;
    const invalidUrl = validateLoginUrl(loginUrl);
    const seconds = Number(waitSeconds);
    if (invalidUrl) { setFeedback({ error: true, message: invalidUrl }); return; }
    if (!/^\d+$/.test(waitSeconds.trim()) || !Number.isInteger(seconds) || seconds < 1 || seconds > 30) {
      setFeedback({ error: true, message: '等待秒数请输入 1 至 30 之间的整数。' });
      return;
    }
    posting.current = true;
    setSaving(true);
    setFeedback(null);
    try {
      const settings = await onSave(environment.id, { mode, login_url: loginUrl.trim(), wait_seconds: seconds });
      if (!mounted.current) return;
      setMode(settings.mode);
      setLoginUrl(settings.login_url);
      setWaitSeconds(String(settings.wait_seconds));
      setEditing(false);
      setFeedback({ error: false, message: '登录检查设置已保存，下次检查时生效。' });
    } catch (reason) {
      if (mounted.current) setFeedback({ error: true, message: reason instanceof Error ? reason.message : '设置未保存，请重试。' });
    } finally {
      posting.current = false;
      if (mounted.current) setSaving(false);
    }
  };
  return <section className="drawer-section login-check-panel" aria-labelledby="login-check-title" data-testid="login-check-panel">
    <h3 id="login-check-title">登录检查设置</h3>
    <form noValidate onSubmit={event => { void save(event); }}>
      <label className="login-check-field" htmlFor="login-check-mode"><span>检查方式</span>
        <select id="login-check-mode" data-testid="login-check-mode" value={mode} disabled={disabled}
          onChange={event => { edit(); setMode(event.target.value as LoginCheckSettings['mode']); }}>
          <option value="url">通用网址检查（推定）</option>
          {environment.login_check_platform ? <option value="platform">平台接口验证</option> : null}
        </select>
      </label>
      {mode === 'url' ? <>
        <label className="login-check-field" htmlFor="login-check-url"><span>登录页地址 <small>可选</small></span>
          <input id="login-check-url" data-testid="login-check-url" type="text" inputMode="url" value={loginUrl} disabled={disabled}
            placeholder="https://example.com/login" autoComplete="off" autoCapitalize="none" spellCheck={false} aria-describedby="login-check-url-help"
            onChange={event => { edit(); setLoginUrl(event.target.value); }} />
        </label>
        <p className="login-check-help" id="login-check-url-help">填写最短的完整登录页地址。最终页面地址包含它就视为需登录，尾部参数无需全部填写；留空使用 login、signin、passport 等通用识别。</p>
      </> : <p className="login-check-help">使用该环境已适配的平台接口验证登录身份，成功后显示“已验证登录”。</p>}
      <label className="login-check-field login-check-wait" htmlFor="login-check-wait"><span>跳转等待秒数</span>
        <input id="login-check-wait" data-testid="login-check-wait" type="number" inputMode="numeric" min="1" max="30" step="1" value={waitSeconds} disabled={disabled}
          onChange={event => { edit(); setWaitSeconds(event.target.value); }} /><small>1–30 秒，默认 5 秒</small>
      </label>
      <p className="login-check-help">{mode === 'url'
        ? '检查时访问配置主页，等待跳转后判断。未命中登录页时显示“推定已登录”。'
        : '检查时访问配置主页，按等待秒数完成页面跳转后，再进行平台接口验证。'}</p>
      <p className="login-check-help">保存仅更新本机设置，重启后保留；已关闭的环境也可保存。</p>
      {dirty ? <p className="login-check-unsaved" data-testid="login-check-unsaved">设置尚未保存，请先保存再检查登录。</p> : null}
      {feedback ? <p className={`login-check-feedback ${feedback.error ? 'login-check-error' : 'login-check-success'}`}
        data-testid="login-check-feedback" role={feedback.error ? 'alert' : 'status'}>{feedback.message}</p> : null}
      <div className="login-check-actions">
        <button className="button button-primary" type="submit" data-testid="login-check-save" disabled={disabled || !dirty}>
          {saving ? <LoaderCircle size={14} className="spin" /> : <Save size={14} />}{saving ? '正在保存…' : '保存检查设置'}
        </button>
        <button className="button button-outline" type="button" data-testid="login-check-run" disabled={disabled || dirty || !environment.running}
          title={dirty ? '请先保存检查设置' : !environment.running ? '请先打开环境' : undefined} onClick={onCheck}>
          <ShieldCheck size={14} />检查登录
        </button>
      </div>
    </form>
  </section>;
}
