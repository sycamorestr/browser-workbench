import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';

const qianniu = await fs.readFile(new URL('./vendor/check_qianniu.js', import.meta.url), 'utf8');
const jst = await fs.readFile(new URL('./vendor/check_jst.js', import.meta.url), 'utf8');
const response = (body, status = 200) => ({
  status, ok: status === 200, url: 'https://business.example/api',
  json: async () => body, text: async () => JSON.stringify(body),
});
const base = { URL, AbortSignal, document: {body: {innerText: ''}} };

const homeUrl = 'https://myseller.taobao.com/home.htm/QnworkbenchHome/';
const homeApi = 'mtop.taobao.jdy.resource.shop.info.get';
const success = data => ({ret: ['SUCCESS::调用成功'], data: {result: data}});
const homeBase = {
  ...base, setTimeout, clearTimeout,
  location: {href: homeUrl},
  performance: {getEntriesByType: () => [{name: `https://h5api.m.taobao.com/h5/${homeApi}/1.0/?ttid=observed-client&sign=not-replayed`}]},
};
let requests = 0;
const runHome = (request, overrides = {}) => vm.runInNewContext(qianniu, {
  ...homeBase,
  window: {lib: {mtop: {request: async options => { requests++; return await request(options); }}}},
  ...overrides,
});
let result = await runHome(async options => {
  assert.equal(options.api, homeApi);
  assert.equal(options.v, '1.0');
  assert.equal(JSON.stringify(options.data), '{}');
  assert.equal(options.ttid, 'observed-client');
  assert.equal(options.LoginRequest, false);
  assert.equal(options.AntiCreep, false);
  assert.equal(options.AntiFlood, false);
  assert.equal(options.sign, undefined, 'signed URLs are never replayed');
  return success({shopId: 123, shopName: '\u00a0 测试  店铺 \n'});
});
assert.equal(result.verified, true);
assert.equal(result.store, '测试 店铺');
assert.equal(requests, 1, 'one fresh home query, without identity comparison or retry');

result = await runHome(async () => success({shopId: 123}));
assert.equal(result.verified, true, 'a current seller result does not require a display name');
assert.equal(result.store, undefined);
result = await runHome(async () => success({shopName: '历史昵称'}));
assert.equal(result.verified, undefined, 'a nickname alone is not authentication evidence');
result = await runHome(async () => ({ret: ['SUCCESS::调用成功'], data: {}}));
assert.equal(result.verified, undefined, 'an empty success envelope is not a seller session');
result = await runHome(async () => ({ret: ['FAIL_BIZ_PERMISSION::无权限']}));
assert.equal(result.verified, undefined);
assert.equal(result.required, undefined, 'permission errors remain distinct from logged-out responses');

for (const code of ['FAIL_SYS_SESSION_EXPIRED', 'FAIL_SYS_TOKEN_EMPTY', 'FAIL_SYS_USER_VALIDATE', 'FAIL_SYS_RGV587_ERROR']) {
  requests = 0;
  result = await runHome(async () => { throw {ret: [code + '::需要人工处理']}; });
  assert.equal(result.required, true);
  assert.equal(requests, 1, 'an authentication failure must never retry');
}
requests = 0;
result = await runHome(async () => { throw Error('unexpected'); }, {document: {body: {innerText: '请先登录'}}});
assert.equal(result.required, true);
assert.equal(requests, 0);
result = await runHome(async () => { throw Error('unexpected'); }, {location: {href: 'https://loginmyseller.taobao.com/'}});
assert.equal(result.required, true);
assert.equal(requests, 0);
result = await runHome(async () => { throw Error('unexpected'); }, {location: {href: 'https://myseller.taobao.com/unrelated'}});
assert.equal(result.verified, undefined);
assert.equal(requests, 0, 'unrelated routes are not probed');

await assert.rejects(runHome(async () => { throw Error('network unavailable'); }), /network_unavailable/);
await assert.rejects(runHome(async () => ({ret: ['FAIL_SYS_REQUEST_TIMEOUT::超时']})), /network_unavailable/);

// Simulate a runtime arriving after DOMContentLoaded without waiting in tests.
let elapsed = 0;
const delayedWindow = {};
requests = 0;
result = await runHome(async () => { throw Error('unexpected'); }, {
  window: delayedWindow,
  Date: {now: () => elapsed},
  setTimeout: (callback, delay) => {
    if (delay === 100) {
      elapsed += delay;
      delayedWindow.lib = {mtop: {request: async () => { requests++; return success({shopId: 456}); }}};
      queueMicrotask(callback);
      return 0;
    }
    return setTimeout(callback, delay);
  },
});
assert.equal(result.verified, true);
assert.equal(requests, 1);
assert.equal(elapsed, 100);
elapsed = 0;
requests = 0;
result = await runHome(async () => { requests++; throw Error('unexpected'); }, {
  window: {}, Date: {now: () => elapsed},
  setTimeout: (callback, delay) => { elapsed += delay; queueMicrotask(callback); return 0; },
});
assert.equal(result.verified, undefined);
assert.equal(result.required, undefined, 'runtime absence is unknown, not automatically logged out');
assert.equal(requests, 0);
assert.equal(elapsed, 5000, 'runtime readiness has a fixed bound');

const tenantContext = {
  ...base,
  location: {href: 'https://src.erp321.com/erp-web-group/erp-scm-invoice-goods/index'},
  performance: {getEntriesByType: () => [
    {name: 'https://jstweb.cn-hangzhou.log.aliyuncs.com/?co_id=test-company&user_id=test-user'},
  ]},
};
let request;
result = await vm.runInNewContext(jst, {
  ...tenantContext,
  fetch: async (url, options) => { request = {url, options}; return response({code: 0, act: 0, data: []}); },
});
assert.equal(result.verified, true);
assert.equal(result.coid, 'test-company');
assert.equal(request.options.credentials, 'include');
assert.equal(JSON.parse(request.options.body).page.pageSize, 1);
assert.equal(JSON.parse(request.options.body).data.sku_id, '@@__BROWSER_WORKBENCH_LOGIN_CHECK__');

result = await vm.runInNewContext(jst, {
  ...tenantContext, fetch: async () => response({message: 'unauthorized'}, 401),
});
assert.equal(result.required, true, 'historic tenant ids must not hide expired authentication');

result = await vm.runInNewContext(jst, {
  ...tenantContext, fetch: async () => response({code: 500, act: 0}),
});
assert.equal(result.verified, false, 'an unavailable business API must not be reported as verified');
await assert.rejects(vm.runInNewContext(jst, {
  ...tenantContext, fetch: async () => { throw Error('network unavailable'); },
}), /network unavailable/);

requests = 0;
result = await vm.runInNewContext(jst, {
  ...tenantContext, performance: {getEntriesByType: () => []},
  fetch: async () => { requests++; throw Error('unexpected'); },
});
assert.equal(requests, 0, 'missing tenant context must never guess a tenant');
assert.equal(result.verified, undefined);
console.log('Auth checks passed with mocked page responses; no live browser requests.');
