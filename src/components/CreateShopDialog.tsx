import { useEffect, useRef, useState, type FormEvent } from 'react';
import { FolderOpen, LoaderCircle, Plus, X } from 'lucide-react';
import type { CreatedShop, CreateShopInput } from '../types';

interface Props {
  defaultParentFolder: string;
  onCancel: () => void;
  onCreated: (shop: CreatedShop) => void;
  onCreate: (input: CreateShopInput, openAfter: boolean) => Promise<CreatedShop>;
  onPickFolder: (signal: AbortSignal) => Promise<string | null>;
}

function homeUrlValidationError(value: string): string | null {
  const address = value.trim();
  if (!address) return '请填写主页地址，例如 https://example.com/。';
  if (address.length > 4096) return '主页地址不能超过 4096 个字符，请检查后重试。';
  if (/[\u0000-\u001f\u007f-\u009f]/.test(value) || /\s/.test(address) || address.includes('\\')) {
    return '主页地址不能包含空白、控制字符或反斜杠，请检查后重试。';
  }
  const authority = /^https?:\/\/([^/?#]+)/i.exec(address)?.[1];
  if (!authority) return '请填写以 http:// 或 https:// 开头、包含主机名的完整主页地址。';
  try {
    const parsed = new URL(address);
    if (!parsed.hostname || !['http:', 'https:'].includes(parsed.protocol)) {
      return '请填写有效的 HTTP 或 HTTPS 主页地址。';
    }
    if (parsed.port === '0') return '主页地址的端口必须在 1 至 65535 之间。';
    if (authority.includes('@') || parsed.username || parsed.password) {
      return '主页地址不能包含账号或密码，请移除地址中的登录凭据。';
    }
  } catch {
    return '主页地址格式不正确，请填写有效的 HTTP 或 HTTPS 完整地址。';
  }
  return null;
}

export default function CreateShopDialog({ defaultParentFolder, onCancel, onCreated, onCreate, onPickFolder }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const homeUrlInput = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);
  const picker = useRef<AbortController | null>(null);
  const savingRef = useRef(false);
  const [draft, setDraft] = useState<CreateShopInput>(() => ({ name: '', home_url: '', parent_folder: defaultParentFolder, login_username: '' }));
  const [openAfter, setOpenAfter] = useState(true);
  const [saving, setSaving] = useState(false);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [homeUrlError, setHomeUrlError] = useState<string | null>(null);
  useEffect(() => {
    mounted.current = true;
    dialog.current?.showModal();
    return () => { mounted.current = false; picker.current?.abort(); };
  }, []);
  const cancel = () => { if (!savingRef.current) onCancel(); };
  const update = (field: keyof CreateShopInput, value: string) => setDraft(current => ({ ...current, [field]: value }));
  const chooseFolder = async () => {
    if (picker.current || savingRef.current) return;
    const request = new AbortController();
    picker.current = request;
    setPicking(true);
    setError(null);
    try {
      const path = await onPickFolder(request.signal);
      if (mounted.current && !request.signal.aborted && path) update('parent_folder', path);
    } catch (reason) {
      if (mounted.current && !request.signal.aborted) setError(reason instanceof Error ? reason.message : '未能选择文件夹，请直接填写路径。');
    } finally {
      picker.current = null;
      if (mounted.current) setPicking(false);
    }
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (savingRef.current || picking) return;
    const input = { name: draft.name.trim(), home_url: draft.home_url.trim(), parent_folder: draft.parent_folder.trim(), login_username: draft.login_username.trim() };
    if (!input.name || !input.parent_folder) { setError('请填写环境名称和存放文件夹。'); return; }
    const invalidHomeUrl = homeUrlValidationError(draft.home_url);
    setHomeUrlError(invalidHomeUrl);
    if (invalidHomeUrl) { setError(null); homeUrlInput.current?.focus(); return; }
    savingRef.current = true;
    setSaving(true);
    setError(null);
    try {
      const shop = await onCreate(input, openAfter);
      if (mounted.current) onCreated(shop);
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : '创建未完成，请检查填写内容后重试。');
    } finally {
      savingRef.current = false;
      if (mounted.current) setSaving(false);
    }
  };
  return <dialog ref={dialog} className="confirm-dialog create-shop-dialog" data-testid="create-shop-dialog" aria-labelledby="create-shop-title"
    onCancel={event => { event.preventDefault(); cancel(); }}>
    <button className="icon-button dialog-close" disabled={saving} aria-label="取消新建环境" onClick={cancel}><X size={19} /></button>
    <div className="confirm-icon create-shop-icon"><Plus size={23} /></div>
    <h2 id="create-shop-title">新建环境</h2>
    <p>创建独立浏览器环境，登录信息单独保存在本机。</p>
    <form noValidate onSubmit={event => { void submit(event); }}>
      <label className="create-field" htmlFor="create-shop-name"><span>环境名称</span><input id="create-shop-name" data-testid="create-shop-name"
        autoFocus required value={draft.name} disabled={saving} placeholder="填写环境名称" onChange={event => update('name', event.target.value)} /></label>
      <label className="create-field" htmlFor="create-home-url"><span>主页地址</span><input ref={homeUrlInput} id="create-home-url" data-testid="create-home-url"
        type="text" inputMode="url" required value={draft.home_url} disabled={saving} placeholder="https://example.com/" autoComplete="off" autoCapitalize="none" spellCheck={false}
        aria-invalid={homeUrlError ? true : undefined} aria-describedby={homeUrlError ? 'create-home-url-help create-home-url-error' : 'create-home-url-help'}
        onChange={event => { update('home_url', event.target.value); setHomeUrlError(null); }} /></label>
      <p className="dialog-help" id="create-home-url-help">填写该环境打开时访问的完整地址，支持 HTTP、HTTPS 和内网站点。</p>
      {homeUrlError ? <p className="dialog-error" id="create-home-url-error" role="alert">{homeUrlError}</p> : null}
      <label className="create-field" htmlFor="create-shop-parent"><span>存放文件夹</span><div className="folder-input-row"><input id="create-shop-parent" data-testid="create-shop-parent"
        required value={draft.parent_folder} disabled={saving} placeholder="例如 D:\浏览器环境" onChange={event => update('parent_folder', event.target.value)} />
        <button className="button button-outline" type="button" data-testid="pick-shop-folder" disabled={saving || picking} onClick={() => { void chooseFolder(); }}>
          {picking ? <LoaderCircle size={14} className="spin" /> : <FolderOpen size={14} />}{picking ? '正在选择' : '选择文件夹'}</button></div></label>
      <p className="dialog-help">请填写绝对路径，将在该文件夹下自动创建独立子目录。</p>
      {picking ? <p className="picker-hint" role="status">请在系统选择目录窗口中操作。取消选择会保留已填内容。</p> : null}
      <label className="create-field" htmlFor="create-shop-login"><span>登录账号 / 备注 <small>可选</small></span><input id="create-shop-login" data-testid="create-shop-login"
        value={draft.login_username} disabled={saving} placeholder="仅作识别，不会自动登录" autoComplete="off" onChange={event => update('login_username', event.target.value)} /></label>
      <label className="dialog-checkbox create-open-option"><input type="checkbox" data-testid="create-shop-open-after" checked={openAfter} disabled={saving}
        onChange={event => setOpenAfter(event.target.checked)} /><span>创建后打开该主页</span></label>
      {error ? <p className="dialog-error" role="alert">{error}</p> : null}
      <footer><button className="button button-outline" type="button" disabled={saving} onClick={cancel}>取消</button>
        <button className="button button-primary" type="submit" data-testid="create-shop-submit" disabled={saving || picking}>
          {saving ? <LoaderCircle size={14} className="spin" /> : <Plus size={14} />}{saving ? '正在创建…' : '创建环境'}</button></footer>
    </form>
  </dialog>;
}
