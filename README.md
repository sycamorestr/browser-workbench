# 浏览器工作台

独立的 Windows 本地浏览器管理面板。读取现有店铺清单，也可创建工作台专用店铺，管理各店铺独立 Edge 环境和共享票聚环境，并提供 Playwright 通过 CDP 连接所需的信息。此项目不依赖已安装的开票 skill，也不执行开票业务。

首次使用从[安装与启动](#安装与启动)开始：仓库提供一店一票聚的空白示例，不附带任何店铺账号或登录数据。

## 日常使用

双击桌面「浏览器工作台」，或运行 `启动工作台.cmd`。面板默认位于 `http://127.0.0.1:17860`，重复启动会复用服务。

- 在环境列表选择店铺，可以批量打开、检查登录、关闭。批量操作排队串行执行，结果逐项显示。
- 「一键打开全部店铺」覆盖清单内全部店铺，不受搜索或状态筛选影响，不包含共享票聚；已打开环境直接复用。
- 「一键关闭全部店铺」逐店保存会话后正常关闭，不受列表筛选影响，不包含共享票聚。确认框默认勾选「同时暂停定时维护」；无论是否勾选，当前手动或定时维护轮都会在当前环境完成后停止，避免随后又打开刚关闭的店铺。未勾选时保留后续定时计划，下一轮仍可能重新打开店铺。
- 全部关闭按队列执行，之前排队的打开操作完成后再关闭。已关闭环境直接记为完成；单店被占用或会话保存失败时保留其窗口并显示原因，其他店铺继续处理。重复点击不会创建第二个全关任务。
- 「新建店铺」可创建独立浏览器环境，并选择创建后打开千牛主页，具体见下节。
- 「定位窗口」切到已有浏览器；「连接详情」查看和复制环境 ID、CDP 地址、调试端口、数据目录、Profile、配置路径、可执行文件和业务页地址。
- 浏览器运行、CDP 可连接、业务已登录是三个独立状态。点击「检查登录」或打开环境后检查实际登录状态；检查时间随结果显示。工作台只确认是否已登录，不比对店铺名、账号或开票主体；读取到的登录信息仅供查看。
- 每家店铺有独立用户目录，票聚共用另一个环境。工作台只打开和维护 `https://myseller.taobao.com/` 千牛主页。订单、发票 URL 由开票 skill 的业务配置维护，开票时再补齐；工作台不恢复这些业务页，也不关闭已经存在的业务标签。窗口关闭后，再次点击「打开」继续使用同一份本机数据，并检查实际登录状态。
- 登录后可在「详情 → 登录态保存」点击「保存登录态」，查看最近保存时间。通过面板关闭环境时会先保存、再正常关闭；保存失败时保留窗口并提示原因。
- 后台服务空闲时约每 60 秒保存一次正在运行环境的 Cookie，这一步不访问网页；另可开启下述定时主页维护。任务占用时让行。直接点浏览器右上角关闭前，建议先确认保存成功，避免最后一次同步后新产生的会话尚未保存。
- 关闭面板页面不会关闭店铺浏览器。面板后台服务停止也不会关闭它们。
- 开票任务占用浏览器时，面板遵守相同的进程锁，避免同时操作。请不要手动删除锁文件。

## 新建店铺环境

点击「新建店铺」，填写店铺名称，选择一个本机已有的父文件夹，并可填写「登录账号 / 备注」。该文本只供识别环境，不保存密码，也不会据此自动登录或匹配账号。

工作台自动分配环境 ID 和固定非零调试端口，并在所选父文件夹下创建独立目录：

```text
所选父文件夹/
└─ shop-xxxxxxxx/
   ├─ browser.json     工作台浏览器配置，只包含千牛主页
   ├─ profile/         此店铺独立的浏览器数据目录
   └─ downloads/       此环境独立的下载目录
```

创建本身只登记环境；对话框默认勾选「创建后打开千牛主页」，可取消，勾选时界面会另行提交打开操作。首次使用需在该窗口完成人工登录，再点击「检查登录」确认并保存会话。以后继续使用同一份数据目录。

新店铺创建后即时显示，无需重启服务，并自动参与工作台的全部打开、全部关闭及后续维护轮次；仍遵守维护开关和人工登录处理规则。新环境不会自动登记到发票业务，也不会生成或修改开票配置。

原始 `shops.json` 及其引用的配置保持只读。自建店铺登记在原清单旁的 `.browser-workbench-shops.json`，运行时与原清单合并，重启后恢复；各自的 `browser.json` 保存在上述独立目录中。

## 定时维护登录态

在「登录态维护」设置执行间隔并开启，默认建议每 2 小时一轮，也可选择 30 分钟、1 小时、4 小时、12 小时或每天。新安装默认关闭；设置保存在店铺清单旁的 `.browser-workbench-maintenance.json`，不改写开票配置。可选择是否同时维护共享票聚。

每轮按店串行：打开关闭的浏览器或复用原窗口 → 复用或恢复千牛主页 → 访问主页让站点正常处理会话 → 确认实际登录 → 保存最新 Cookie。共享票聚选中时独立维护一次。浏览器保持打开，不循环关闭再启动，不修改订单或发票。单店被占用则跳过，失败不妨碍其他店；排队的手动操作在店铺之间优先执行。

「现在维护一次」可在定时关闭时单独运行。维护轮次不重叠；暂停后当前店完成，尚未开始的定时维护店铺取消。明确登录失效的环境会标记为需要人工登录，后续定时轮跳过；人工登录后点击「检查登录」成功即可恢复，也可手动维护一次重新检查。面板显示下一轮时间、最近一轮时间和逐店结果。

关闭面板网页后，本地服务仍负责计时；退出服务、关机或睡眠期间不会运行，服务恢复后错过的轮次最多补一次。启用定时维护后，手动关闭的店铺浏览器可能在下一轮重新打开；需要保持关闭时先暂停维护。主页访问有助于更新平台允许续期的会话，但无法延长平台的强制失效时间，也不会自动通过验证码。

## 安装与启动

本版面向 **Windows + Microsoft Edge**，需先安装 Edge、Git、Python 3.11+（含 `py` 启动器）和 Node.js 20.19+（含 npm）。构建使用前端 React/Vite，后端只需 Python Playwright；不需要 OpenCLI、浏览器扩展、Electron，也无需运行 `playwright install` 下载浏览器。

### 1. 获取代码并安装依赖

在准备存放代码的目录打开 PowerShell，运行：

```powershell
git clone https://github.com/sycamorestr/browser-workbench.git
Set-Location .\browser-workbench
py -3 -m venv .venv
$workbenchPython = (Resolve-Path '.\.venv\Scripts\python.exe').Path
& $workbenchPython -m pip install -r .\requirements.txt
npm ci
npm run build
```

后续命令继续在仓库根目录、同一个 PowerShell 窗口执行；重新打开窗口时，重新设置 `$workbenchPython` 即可，不需要激活虚拟环境。

### 2. 将空白配置复制到仓库之外

以下默认把私有文件放在本机 `%LOCALAPPDATA%\BrowserWorkbenchData`。可以将 `$privateRoot` 改成自己的绝对路径，但必须放在仓库目录之外。重复执行会保留已有配置：

```powershell
$privateRoot = Join-Path $env:LOCALAPPDATA 'BrowserWorkbenchData'
New-Item -ItemType Directory -Path $privateRoot -Force | Out-Null
$exampleNames = @{
    'registry.example.json' = 'shops.json'
    'shop.example.json' = 'shop01.json'
    'piaoju.example.json' = 'piaoju.json'
}
foreach ($sourceName in $exampleNames.Keys) {
    $destination = Join-Path $privateRoot $exampleNames[$sourceName]
    if (-not (Test-Path -LiteralPath $destination)) {
        Copy-Item -LiteralPath (Join-Path '.\examples' $sourceName) -Destination $destination
    }
}
$registry = Join-Path $privateRoot 'shops.json'
```

私有目录最初有三个 JSON；浏览器首次打开后会创建对应的数据和下载目录：

```text
BrowserWorkbenchData/
├─ shops.json              店铺清单，引用下面两份配置
├─ shop01.json             首个店铺浏览器配置
├─ piaoju.json             共享票聚浏览器配置
├─ profiles/
│  ├─ shop01/              首个店铺独立登录数据
│  └─ piaoju/              共享票聚独立登录数据
└─ downloads/
   ├─ shop01/
   └─ piaoju/
```

`shops.json` 中 `browser_config` / `jst_browser_config` 相对于清单所在目录解析，浏览器配置中的 `user_data_dir` / `download_dir` 相对于该配置文件所在目录解析。保留以上文件名和层级即可直接使用。

首次示例店铺名为「我的店铺」。启动前可在私有 `shops.json` 中把 `store` 改为自己的显示名称，`login_username` 可以为空；后续店铺通过界面「新建店铺」添加，不必继续复制 JSON。这里的名称和备注不代替平台登录，也不进行店铺主体匹配。

当前清单需保留共享票聚配置，但**可以暂不打开或登录票聚**：先使用千牛店铺即可。若开启定时维护且暂不使用票聚，请取消「同时维护共享票聚」。需要时再单独打开共享票聚完成人工登录。

示例使用两个不同的固定非零 CDP 端口：店铺 `19221`、票聚 `19222`，与面板端口 `17860` 分开。遇到端口冲突，先确认该环境没有运行，再修改私有配置中的 `remote_debugging_port` 为未被占用且与其他环境不同的端口，并重启工作台后台服务。不能通过换端口绕过仍在运行的浏览器、目录占用或归属检查；应先正常关闭原环境。

### 3. 安装本机入口并启动

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-Local.ps1 -Registry $registry -Python $workbenchPython
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-Workbench.ps1
```

面板打开后，点击「我的店铺」一行的「打开」，在专用 Edge 窗口完成人工登录，再点击「检查登录」。此后双击桌面「浏览器工作台」或仓库中的 `启动工作台.cmd` 即可。专用环境使用示例指定的独立数据目录，不复用日常 Edge 的个人资料目录。

本机入口配置位于 `%LOCALAPPDATA%\BrowserWorkbench\settings.json`，包含清单路径、虚拟环境 Python 路径和面板端口；服务日志也在此目录。安装后不要移动代码目录或 `.venv`；移动后应重新运行 `Install-Local.ps1` 更新入口。

调试时也可不安装桌面入口，直接在仓库根目录前台运行服务，随后打开 [本机面板](http://127.0.0.1:17860)：

```powershell
& $workbenchPython -m backend.server --registry $registry --port 17860
```

在界面中新建店铺会立即生效。手工修改原始清单、浏览器配置或端口后，需重启面板后台服务。前端修改后执行 `npm run build`，再刷新 Python 服务提供的页面进行联调，确保请求与本地 API 同源。生产运行只需 Python 服务和构建好的 `dist`，不需要 Node.js 常驻。

### 私有文件与公开分享

仓库的 `examples/*.example.json` 仅含虚构名称、公开页面 URL 和相对目录。真实 `shops.json`、店铺配置、自建店铺登记文件、会话维护记录、整个浏览器 Profile、Cookie、账号备注、下载文件和服务日志都应留在仓库之外；不要提交到 Git，也不要随问题反馈上传。分享项目代码时仅分享空白示例，不能把登录环境复制到公开仓库。

## 代码结构与连接原理

```text
src/                         React 页面、列表、连接详情、批量操作
backend/server.py            本地 HTTP API、静态页面、同源与请求令牌校验
backend/browser_service.py   环境登记、进程检查、串行作业队列、窗口与登录管理
backend/shop_registry.py     自建店铺登记、独立目录与固定端口分配，不改原清单
backend/folder_picker.py     本机文件夹选择对话框，供新建店铺选择父目录
backend/maintenance.py       定时维护设置、上轮结果与人工登录待处理标记的持久化
backend/session_cookies.py   原目录内会话 Cookie 持久化与不含 Cookie 值的保存状态
backend/vendor/             独立固定版本的浏览器控制器、锁及只读登录检查脚本
tests/                       HTTP 边界、配置与浏览器管理测试
examples/                    首次安装用的空白清单与独立浏览器配置
Start-Workbench.ps1          复用/启动服务并打开面板
Install-Local.ps1            本地入口配置及桌面快捷方式
```

Edge 使用普通进程启动，沿用配置中的独立 `user_data_dir`、`profile_directory` 和固定非零调试端口；调试仅绑定本机。启动不添加 `--enable-automation`、`--remote-debugging-pipe` 或端口 `0`。控制器再使用 `connect_over_cdp` 附加到该浏览器。工作台在内存中为千牛构造独立的 `home` 页面配置，不改原文件中的 `invoice/orders`；主页跳转后的商家工作台地址也可复用，已有业务页不会被当成主页覆盖。共享票聚继续复用其独立环境。

外部 Playwright 程序可使用详情面板复制的 CDP 地址：

```python
from playwright.async_api import async_playwright

async def inspect(endpoint):
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(endpoint)
        for context in browser.contexts:
            for page in context.pages:
                print(page.url)
        # 结束本次附加连接；不发送 CDP Browser.close。
        await browser.close()
```

正式业务任务应同时遵守数据目录下的 `.qianniu-browser.lock`，不能只凭端口号并发抢占浏览器。面板可复用原 skill 的浏览器身份配置及锁协议，代码、进程和界面相互独立；工作台自建店铺只在工作台登记，需要接入开票业务时须另行配置。

状态轮询约每 5 秒，进程清单缓存 4 秒；轮询不发业务查询、不打开标签页。页面显示最近一次明确检查的登录结果，15 分钟后标记为待重新检查，不承诺会话永远有效。动作日志保留本次服务进程最近 50 条，重启后清空。

## 关闭后恢复登录态

本版参考 [DSH 平台账号管理器](https://github.com/sycamorestr/dsh-platform-account-manager-plugin) 的会话 Cookie 持久化机制，保留固定目录和关闭前同步的做法。版权说明见 `THIRD_PARTY_NOTICES.md`。

1. 用户在自己的店铺环境完成人工登录。
2. 点击「检查登录」确认已登录，成功后自动保存；也可在详情中单独保存。
3. 同步只把业务域内可处理的会话 Cookie 转为本机保留 30 天，由 Chromium 写回原 Cookie 存储。已有明确过期时间的 Cookie 不延长，Host-only、HttpOnly、Secure、SameSite 和可识别的分区属性保持。
4. 面板关闭环境前再次同步，并等待该环境的浏览器进程正常退出。下次启动同一目录时，浏览器直接加载保存的数据，面板再检查实际登录状态。

不会导出或跨店注入 Cookie，不保存另一份可被恢复的旧登录快照。用户在平台主动退出后，不会用旧快照重新登录。Local Storage、IndexedDB 等继续由原 Profile 保存。`.browser-workbench-cookie-sync.json` 仅记录同步状态、时间和数量，不包含 Cookie 名称、值或密码。

“已保存”表示本机数据同步完成，不等于“已登录”，也不是 30 天免登录承诺。平台撤销会话、令牌过期、要求验证码或新的设备验证时，仍需人工处理。断电、浏览器崩溃或直接关窗口发生在最近一次同步前，也可能丢失刚创建的会话。

## 验证

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
node backend/test_auth_scripts.mjs
npm run build
```

HTTP 服务只监听 `127.0.0.1`；动作需要本机来源及当前请求令牌，API 只接受登记的环境 ID 和固定动作，不执行任意命令。正常关闭针对归属校验后的单个浏览器，通过 CDP 发出关闭，不使用全局 `taskkill`。详情中的本地 CDP 地址用于本机连接，不应配置为公网服务。
