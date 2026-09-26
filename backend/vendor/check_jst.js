(async () => {
  if (/login|passport/i.test(location.href) || /请先登录|登录失效/.test(document.body?.innerText || '')) {
    return {required: true};
  }
  const pairs = new Map();
  for (const entry of performance.getEntriesByType('resource')) {
    try {
      const url = new URL(entry.name);
      if (url.hostname !== 'jstweb.cn-hangzhou.log.aliyuncs.com') continue;
      const coid = url.searchParams.get('co_id'), uid = url.searchParams.get('user_id');
      if (coid && uid) pairs.set(`${coid}:${uid}`, {coid, uid});
    } catch (_) {}
  }
  if (pairs.size !== 1) return {};
  const tenant = [...pairs.values()][0];
  // Verify the current session with a bounded, read-only product query. The
  // DOM label and resource timings alone may describe an expired session.
  const response = await fetch('https://apiweb.erp321.com/webapi/ItemApi/ItemSku/GetPageListV2', {
    method: 'POST', credentials: 'include', signal: AbortSignal.timeout(6000),
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({
      page: {currentPage: 1, pageSize: 1, hasPageInfo: false, pageAction: 1},
      data: {sku_id: '@@__BROWSER_WORKBENCH_LOGIN_CHECK__', queryFlds: ['sku_id']},
      ip: '', coid: tenant.coid, uid: tenant.uid,
    }),
  });
  if ([401, 403].includes(response.status) || /login|passport/i.test(response.url)) return {required: true};
  const content = await response.text();
  if (/请先登录|未登录|登录失效|登录超时|unauthorized|session expired|login required/i.test(content)) {
    return {required: true};
  }
  if (!response.ok) return {};
  let parsed;
  try { parsed = JSON.parse(content); } catch (_) { return {}; }
  if ([401, 403].includes(Number(parsed.code))) return {required: true};
  return {...tenant, verified: parsed.code === 0 && parsed.act === 0};
})()
