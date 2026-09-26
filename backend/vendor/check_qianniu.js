(async () => {
  const api = 'mtop.taobao.jdy.resource.shop.info.get';
  const needsLogin = () => {
    const url = new URL(location.href);
    return /(^|\.)login(?:myseller)?\.taobao\.com$/.test(url.hostname)
      || /请先登录|登录失效|滑动验证|扫码登录/.test(document.body?.innerText || '');
  };
  if (needsLogin()) return {required: true};
  const url = new URL(location.href);
  if (url.hostname !== 'myseller.taobao.com'
      || !['/', '/home.htm', '/home.htm/QnworkbenchHome'].includes(url.pathname.replace(/\/$/, '') || '/')) return {};

  // The home runtime is often loaded after DOMContentLoaded. Wait for it
  // once; do not navigate, replay signed URLs, or retry authentication.
  const readyDeadline = Date.now() + 5000;
  while (typeof window.lib?.mtop?.request !== 'function') {
    if (needsLogin()) return {required: true};
    if (Date.now() >= readyDeadline) return {};
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  const options = {
    api, v: '1.0', data: {}, type: 'GET', dataType: 'json',
    H5Request: true, timeout: 6000,
    LoginRequest: false, AntiCreep: false, AntiFlood: false,
  };
  const observed = performance.getEntriesByType('resource')
    .find(entry => entry.name.includes(`/h5/${api}/1.0/`));
  if (observed) {
    const ttid = new URL(observed.name).searchParams.get('ttid');
    if (ttid) options.ttid = ttid;
  }

  let timer;
  let value;
  try {
    value = await Promise.race([
      Promise.resolve().then(() => window.lib.mtop.request(options)),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(Error('request_timeout')), 6500);
      }),
    ]);
  } catch (error) {
    // MTOP rejects with a structured response for expired sessions and
    // permission errors. Preserve that classification without leaking it.
    if (Array.isArray(error?.ret)) value = error;
    else throw Error(error?.message === 'request_timeout' ? 'request_timeout' : 'network_unavailable');
  } finally {
    clearTimeout(timer);
  }
  const codes = Array.isArray(value?.ret)
    ? value.ret.map(item => String(item).split('::')[0]) : [];
  if (codes.some(code => /SESSION_EXPIRED|TOKEN_EMPTY|TOKEN_EXPIRED|NEED_LOGIN|NOT_LOGIN|USER_VALIDATE|RGV587/.test(code))) {
    return {required: true};
  }
  if (codes.some(code => /TIMEOUT|NETWORK_ERROR|REQUEST_ABORT/.test(code))) throw Error('network_unavailable');
  if (!codes.includes('SUCCESS')) return {};
  const current = value?.data?.result;
  // Success must contain a current seller result, not a cached nickname or
  // an empty success envelope. No comparison with the locally named shop.
  if (!current || typeof current !== 'object' || Array.isArray(current)
      || !/^\d+$/.test(String(current.shopId || '')) || Number(current.shopId) <= 0) return {};
  const store = String(current.shopName || current.displayNick || current.nick || '')
    .replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
  return store ? {verified: true, store} : {verified: true};
})()
