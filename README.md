# 浏览器工作台

独立的 Windows 本地多浏览器管理面板，为每个环境管理独立用户目录、固定调试端口和登录会话，并向 AI、Playwright 等自动化程序提供 CDP 连接信息。全部浏览器在同一列表中管理，不区分店铺、共享等业务角色；业务任务由接入的程序负责。

首次使用从[安装与启动](#安装与启动)开始：默认从空白环境清单启动，再通过网页填写自己的主页地址并新建环境。无需任何特定业务平台，也不附带账号或登录数据。

## 日常使用

双击桌面「浏览器工作台」，或运行 `启动工作台.cmd`。面板默认位于 `http://127.0.0.1:17860`，重复启动会复用服务。

- 在环境列表选择浏览器，可以批量打开、检查登录、关闭。批量操作排队串行执行，结果逐项显示。
- 「一键打开全部环境」覆盖全部活动环境，不受搜索或状态筛选影响；已打开环境直接复用，已归档环境不参与。
- 「一键关闭全部环境」逐个保存会话后正常关闭，覆盖全部活动环境，不受列表筛选影响。确认框默认勾选「同时暂停定时维护」；无论是否勾选，当前手动或定时维护轮都会在当前环境完成后停止。未勾选时保留后续定时计划，下一轮仍可能重新打开环境。
- 全部关闭按队列执行，之前排队的打开操作完成后再关闭。已关闭环境直接记为完成；单个环境被占用或会话保存失败时保留其窗口并显示原因，其他环境继续处理。重复点击不会创建第二个全关任务。
- 「新建环境」可创建独立浏览器环境，并选择创建后打开配置页面，具体见下节。
- 「定位窗口」切到已有浏览器；「连接详情」查看和复制环境 ID、CDP 地址、调试端口、数据目录、Profile、配置路径、可执行文件和业务页地址。
- 浏览器运行、CDP 可连接、登录检查结果是三个独立状态。打开环境或点击「检查登录」会访问配置主页再检查；检查时间随结果显示。「推定已登录」来自通用页面跳转判断，「已验证登录」来自可选的平台接口验证，二者明确区分；不比对店铺名、账号或开票主体。
- 每个环境拥有独立用户目录、固定 CDP 端口和连接详情。窗口关闭后，再次点击「打开」继续使用同一份本机数据，并检查实际登录状态。业务角色不会影响列表展示、搜索、选择和批量操作范围。
- 登录后可在「详情 → 登录态保存」点击「保存登录态」，查看最近保存时间。通过面板关闭环境时会先保存、再正常关闭；保存失败时保留窗口并提示原因。
- 后台服务空闲时约每 60 秒保存一次正在运行环境的 Cookie，这一步不访问网页；另可开启下述定时主页维护。任务占用时让行。直接点浏览器右上角关闭前，建议先确认保存成功，避免最后一次同步后新产生的会话尚未保存。
- 关闭面板页面不会关闭浏览器。面板后台服务停止也不会关闭它们。
- 点击页面右上角「停止工作台」，确认后退出本地后台服务，停止定时维护与自动保存。已提交的手动操作会先完成，当前维护环境结束后跳过其余维护；收尾期间页面显示进度，端口释放后显示「工作台已停止」。停止工作台本身不关闭浏览器。再次使用时双击桌面「浏览器工作台」，原定时维护设置继续生效。
- 如需连浏览器一起关闭，先完成「一键关闭全部环境」（建议保留勾选「同时暂停定时维护」），最后点击「停止工作台」。
- 外部自动化任务占用浏览器时，面板遵守相同的进程锁，避免同时操作。请不要手动删除锁文件。

## 新建浏览器环境

点击「新建环境」，填写环境名称和必填的「主页地址」，选择一个本机已有的父文件夹，并可填写「登录账号 / 备注」。主页地址由使用者自行指定，支持完整的 HTTP / HTTPS 地址、本机和内网站点；不会预填或改写成指定平台。路径、查询参数和页面锚点会一并保存。备注只供识别环境，不保存密码，也不会据此自动登录或匹配账号。

工作台自动分配环境 ID 和固定非零调试端口，并在所选父文件夹下创建独立目录：

```text
所选父文件夹/
└─ shop-xxxxxxxx/
   ├─ browser.json     工作台浏览器配置，保存自行填写的主页地址
   ├─ profile/         此环境独立的浏览器数据目录
   └─ downloads/       此环境独立的下载目录
```

创建本身只登记环境；对话框默认勾选「创建后打开该主页」，可取消，勾选时界面会另行提交打开操作。以后打开、复用和定时维护均使用该环境保存的主页地址，重启工作台后仍然有效。详情中的「主页地址」可以完整查看和复制。

所有环境默认使用通用登录检查。创建后在「环境详情 → 登录检查设置」填写该站点的登录页短地址，例如 `https://accounts.example.com/login`，无需填上跳转后附带的全部参数。检查时先访问配置主页，等待所设置的时间（默认 5 秒，可设 1–30 秒），最终 URL 包含这段地址就标记「需登录」。短地址由使用者指定，检查采用包含匹配，不要求完整 URL 相等，也不要求登录页与主页同域。

登录页地址留空时使用通用登录地址特征，例如 `login`、`signin`、`passport`；默认规则检查域名、路径和 hash 路由，不把普通查询参数中的返回地址当作当前登录页。填写短地址后以该地址为准。页面正常加载且未命中登录地址时显示「推定已登录」，加载超时、错误响应或持续跳转显示「检查失败」。推定状态不能排除公开首页、原页面登录弹窗等情况，主页应选择需要登录的后台页面。

登录检查设置按环境保存，保存本身不打开或跳转浏览器；刷新、重启后继续使用。在详情里保存后可直接「检查登录」，打开环境与定时维护也使用同一规则。已适配的旧站点可选择「平台接口验证」作为兼容选项；其他站点不会执行平台专用脚本。

新环境创建后即时显示，无需重启服务，并自动参与工作台的全部打开、全部关闭及后续维护轮次；仍遵守维护开关和人工登录处理规则。新环境不会自动登记到发票业务，也不会生成或修改开票配置。

原始 `shops.json` 及其引用的配置保持只读。自建环境登记在原清单旁的 `.browser-workbench-shops.json`，运行时与原清单合并，重启后恢复；各自的 `browser.json` 保存在上述独立目录中。每个环境的登录检查规则另存于清单旁的 `.browser-workbench-login-checks.json`，不改动原始浏览器配置。

## 归档、恢复与删除登记

填写错误或暂时不用的环境可在详情中「归档环境」。先关闭该环境并等待任务完成，再确认归档。环境随即移到「已归档」列表，不参与活动环境统计、批量打开/关闭、定时维护及后台自动保存；浏览器配置、登录数据、下载文件和登录检查规则全部保留。

在「已归档」中点击「恢复环境」，会以原环境 ID、浏览器数据目录、调试端口和登录规则重新加入活动列表，登录状态重新检查。归档期间仍可查看目录；若由其他程序打开了该浏览器，可在归档列表中正常关闭，或恢复后继续管理。

确定不需要这个登记时，在已归档列表选择「删除登记」，输入完整环境名确认。删除后工作台不再显示该条目，也不提供恢复入口；本机浏览器目录、配置及下载文件仍保留。可随后新建同名环境，工作台会分配新的 ID、目录和端口，避免重用旧登录数据。归档与删除登记都要求浏览器已关闭、没有排队任务或外部占用；保存失败会保留原状态。

归档与删除状态保存于原清单旁的 `.browser-workbench-lifecycle.json`，刷新、重启后持续生效。导入环境也使用此独立文件记录状态，原始登记文件和浏览器配置保持只读。

## 定时维护登录态

在「登录态维护」设置执行间隔并开启，默认建议每 2 小时一轮，也可选择 30 分钟、1 小时、4 小时、12 小时或每天。新安装默认关闭；设置保存在清单旁的 `.browser-workbench-maintenance.json`。维护范围统一为全部活动环境，不再提供共享环境开关，已归档和已删除登记的环境不参与。

每轮按环境串行：打开关闭的浏览器或复用原窗口 → 访问配置主页 → 等待跳转并按该环境规则判断登录 → 检查通过后保存最新 Cookie。通用检查通过为「推定已登录」，平台验证通过为「已验证登录」。浏览器保持打开。单个环境被占用则跳过，失败不妨碍其他环境；排队的手动操作在环境之间优先执行。

「现在维护一次」可在定时关闭时单独运行。维护轮次不重叠；暂停后当前环境完成，尚未开始的定时维护环境取消。明确登录失效的环境会标记为需要人工登录，后续定时轮跳过；人工登录后点击「检查登录」成功即可恢复，也可手动维护一次重新检查。面板显示下一轮时间、最近一轮时间和逐环境结果。

关闭面板网页后，本地服务仍负责计时；退出服务、关机或睡眠期间不会运行，服务恢复后错过的轮次最多补一次。启用定时维护后，手动关闭的浏览器可能在下一轮重新打开；需要保持关闭时先暂停维护。主页访问有助于更新平台允许续期的会话，但无法延长平台的强制失效时间，也不会自动通过验证码。

## 安装与启动

本版推荐 **Windows + Microsoft Edge**，需先安装 Edge、Git、Python 3.11+（含 `py` 启动器）和 Node.js 20.19+（含 npm）。前端使用 React/Vite，后端依赖 Python Playwright；不需要 OpenCLI、浏览器扩展、Electron，也无需运行 `playwright install` 下载浏览器。网页新建环境默认使用 Edge；已有配置也支持 Chrome。仅使用 Chrome 时，可采用后文的手工导入示例，在私有浏览器配置中设置 `"browser": "Chrome"` 或指定浏览器可执行文件。

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

### 2. 将空白清单复制到仓库之外

以下默认把私有文件放在本机 `%LOCALAPPDATA%\BrowserWorkbenchData`。可以将 `$privateRoot` 改成自己的绝对路径，但必须放在仓库目录之外。重复执行会保留已有清单：

```powershell
$privateRoot = Join-Path $env:LOCALAPPDATA 'BrowserWorkbenchData'
New-Item -ItemType Directory -Path $privateRoot -Force | Out-Null
$registry = Join-Path $privateRoot 'shops.json'
if (-not (Test-Path -LiteralPath $registry)) {
    Copy-Item -LiteralPath '.\examples\registry.example.json' -Destination $registry
}
$environmentRoot = Join-Path $privateRoot 'environments'
New-Item -ItemType Directory -Path $environmentRoot -Force | Out-Null
```

`shops.json` 沿用历史文件名，内容是通用环境登记入口。默认示例为空，不绑定任何站点，也不要求票聚配置：

```json
{
  "schema_version": 1,
  "shops": []
}
```

安装后通过网页「新建环境」添加环境，并把上面创建的 `environments` 文件夹选为父文件夹。填写自己的主页地址，例如内部管理系统的完整 HTTP / HTTPS 地址；新建弹窗不会预填平台网址。私有文件随后按需生成：

```text
BrowserWorkbenchData/
├─ shops.json                              原始空白清单，保持只读
├─ .browser-workbench-shops.json            网页创建的环境登记
├─ .browser-workbench-login-checks.json     各环境登录检查规则
├─ .browser-workbench-lifecycle.json        归档与删除登记状态
├─ .browser-workbench-maintenance.json      定时维护设置与状态
└─ environments/
   └─ shop-xxxxxxxx/
      ├─ browser.json                      用户填写的主页与浏览器配置
      ├─ profile/                          独立浏览器登录数据
      └─ downloads/                        独立下载目录
```

工作台为新环境自动分配不同的固定非零 CDP 端口，与面板端口 `17860` 分开。遇到端口冲突，先确认环境没有运行，再修改对应私有 `browser.json` 中的 `remote_debugging_port`，并重启后台。不能通过换端口绕过仍在运行的浏览器、目录占用或归属检查；应先正常关闭原环境。

### 3. 安装本机入口并启动

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-Local.ps1 -Registry $registry -Python $workbenchPython
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-Workbench.ps1
```

面板打开后，点击「新建环境」，填写名称、主页地址并选择父文件夹。勾选「创建后打开该主页」时，会打开该环境的专用浏览器；也可取消勾选，仅保存登记。需要登录的站点由用户在该窗口完成人工登录，然后到详情配置登录页短地址、检查登录并保存会话。通用检查显示「推定已登录」；支持的平台可另外选择接口验证。

此后双击桌面「浏览器工作台」或仓库中的 `启动工作台.cmd` 即可。专用环境使用独立数据目录，不复用日常浏览器的个人资料目录。右上角「停止工作台」可退出后台服务；若还要关闭活动浏览器，先完成「一键关闭全部环境」。

本机入口配置位于 `%LOCALAPPDATA%\BrowserWorkbench\settings.json`，包含清单路径、虚拟环境 Python 路径和面板端口；服务日志也在此目录。安装后不要移动代码目录或 `.venv`；移动后应重新运行 `Install-Local.ps1` 更新入口。

调试时也可不安装桌面入口，直接在仓库根目录前台运行服务，随后打开[本机面板](http://127.0.0.1:17860)：

```powershell
& $workbenchPython -m backend.server --registry $registry --port 17860
```

在界面中新建环境、保存登录检查规则、归档或恢复会立即生效。手工修改原始清单、浏览器配置或端口后，需重启面板后台服务。前端修改后执行 `npm run build`，再刷新 Python 服务提供的页面进行联调，确保请求与本地 API 同源。生产运行只需 Python 服务和构建好的 `dist`，不需要 Node.js 常驻。

### 可选：手工导入与历史平台兼容

默认安装只需要 `registry.example.json`。`examples` 还提供以下可选模板，均无账号或会话数据：

| 文件 | 用途 |
| --- | --- |
| `registry.example.json` | 推荐的空白清单，通过网页新建环境。 |
| `registry.with-environment.example.json` | 含一个通用环境的手工清单；复制到私有目录后命名为 `shops.json`。 |
| `browser.example.json` | 通用浏览器模板，主页为公开的 `https://example.com/`；与上一份清单配套，复制后命名为 `browser.json`。示例站点没有登录流程，不可作为已登录的验证依据。 |
| `shop.example.json` | 可选的历史千牛主页配置，保留旧文件名供已有流程兼容。 |
| `piaoju.example.json` | 可选的历史票聚商品页配置，供已有环境兼容。 |

手工示例应复制到单独的私有目录再修改。清单中的 `browser_config` 相对于清单所在目录解析；浏览器配置中的 `user_data_dir` / `download_dir` 相对于该配置文件所在目录解析。每个配置应使用不同的数据目录和固定非零调试端口。配置主页由 `browser_sessions.home.url` 指定；历史 `jst_browser_config` 是可选导入入口，缺失时不影响安装、启动或网页新建环境。

历史平台模板不会随默认空白安装自动导入。需要时可将对应配置登记到自己的清单；导入后与其他环境在同一列表管理，仍默认使用通用登录检查。千牛和票聚已适配的接口验证只在详情中作为可选检查方式提供。

### 私有文件与公开分享

仓库的 `examples/*.example.json` 仅含虚构名称、公开页面 URL 和相对目录。真实清单、浏览器配置、自建环境登记、登录检查规则、归档记录、会话维护记录、整个浏览器 Profile、Cookie、账号备注、下载文件和服务日志都应留在仓库之外；不要提交到 Git，也不要随问题反馈上传。分享项目代码时仅分享空白或通用示例，不能把登录环境复制到公开仓库。

## 代码结构与连接原理

工作台的新建和全部关闭接口分别为 `POST /api/environments`、`POST /api/environments/close-all`；旧 `/api/shops` 路由继续兼容，但“全部关闭”同样覆盖全部环境。`GET /api/state` 统一返回 `kind: "browser"`，环境详情提供各自的 CDP 地址。维护设置使用 `{ "enabled": false, "interval_minutes": 120 }`；旧 `include_shared` 布尔字段仅兼容读取，不再影响范围。

新建接口必须提交 `home_url`，例如 `{ "name": "内部后台", "home_url": "http://intranet/admin?team=one#home", "parent_folder": "D:\\浏览器环境", "login_username": "" }`。地址缺失、格式不合法或包含账号密码时会直接拒绝，不创建环境目录。`GET /api/state` 与创建响应中的 `home_url` 保留完整主页地址。

`POST /api/environments/login-check` 保存单个环境的规则，例如 `{ "environment_id": "shop01", "mode": "url", "login_url": "https://accounts.example.com/login", "wait_seconds": 5 }`。模式为 `url` 或适配站点可选的 `platform`；空 `login_url` 使用通用规则。接口遵守同源和 CSRF 校验，环境忙碌时拒绝修改，成功保存后旧登录判断重置为未检查。`GET /api/state` 各环境的 `login_check` 返回生效设置，`login_check_platform` 表示可选平台验证；`auth.status` 中 `assumed` 表示推定、`verified` 表示接口验证。

`POST /api/environments/archive` 和 `/api/environments/restore` 接收 `{ "environment_id": "shop01" }`；`POST /api/environments/delete` 额外要求精确的 `confirm_name`，且环境须已归档。返回 `{ "environment_id": "shop01", "state": "archived" }`，状态可为 `active`、`archived`、`deleted`。不接受文件路径或删除磁盘文件的参数。`GET /api/state` 的 `environments` 仅包含活动环境，`archived_environments` 返回归档列表及 `archived_at`；已有批量接口和维护仅作用于活动列表。

历史 `shops.json`、`.browser-workbench-shops.json` 及环境 ID 继续沿用，避免改动现有登录目录和 AI 连接。`jst_browser_config` 为可选的旧配置导入入口，导入后与其他环境平等管理。空白安装可使用 `{ "schema_version": 1, "shops": [] }`。已有 `browser_sessions.home` 会直接采用；仅历史千牛订单/发票配置没有主页时保留旧的主页兼容处理，已有票聚环境继续使用原配置页。普通网站不附加这些平台的 Cookie 域范围。

```text
src/                         React 页面、列表、连接详情、批量操作
backend/server.py            本地 HTTP API、静态页面、同源与请求令牌校验
backend/browser_service.py   环境登记、进程检查、串行作业队列、窗口与登录管理
backend/shop_registry.py     自建环境登记、独立目录与固定端口分配，不改原清单
backend/folder_picker.py     本机文件夹选择对话框，供新建环境选择父目录
backend/maintenance.py       定时维护设置、上轮结果与人工登录待处理标记的持久化
backend/login_settings.py    每个环境的登录页地址、检查方式、等待时间与独立持久化
backend/lifecycle.py         环境归档、恢复和删除登记状态，保留原文件与资源占用记录
backend/login_probe.py       通用主页访问、跳转等待、短地址包含判断和加载结果检查
backend/session_cookies.py   原目录内会话 Cookie 持久化与不含 Cookie 值的保存状态
backend/vendor/             独立固定版本的浏览器控制器、锁及只读登录检查脚本
tests/                       HTTP 边界、配置与浏览器管理测试
examples/                    空白清单、通用浏览器与可选历史兼容配置
Start-Workbench.ps1          复用/启动服务并打开面板
Install-Local.ps1            本地入口配置及桌面快捷方式
```

Edge 使用普通进程启动，沿用配置中的独立 `user_data_dir`、`profile_directory` 和固定非零调试端口；调试仅绑定本机。启动不添加 `--enable-automation`、`--remote-debugging-pipe` 或端口 `0`。控制器再使用 `connect_over_cdp` 附加到该浏览器。普通主页按保存的路径、查询参数和锚点匹配，避免复用其他工作区的同名页面；已存在的无关标签不会被覆盖。旧站点页面兼容只作用于对应站点。

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

正式业务任务应同时遵守数据目录下的 `.qianniu-browser.lock`，不能只凭端口号并发抢占浏览器。面板可复用原 skill 的浏览器身份配置及锁协议，代码、进程和界面相互独立；新建环境只在工作台登记，外部任务通过连接信息及锁协议接入。

状态轮询约每 5 秒，进程清单缓存 4 秒；轮询不发业务查询、不打开标签页。页面显示最近一次明确检查的登录结果，15 分钟后标记为待重新检查，不承诺会话永远有效。动作日志保留本次服务进程最近 50 条，重启后清空。

## 关闭后恢复登录态

本版参考 [DSH 平台账号管理器](https://github.com/sycamorestr/dsh-platform-account-manager-plugin) 的会话 Cookie 持久化机制，保留固定目录和关闭前同步的做法。本项目采用 [MIT 许可证](LICENSE)，第三方版权说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

1. 用户在自己的浏览器环境完成人工登录。
2. 点击「检查登录」确认已登录，成功后自动保存；也可在详情中单独保存。
3. 同步只把业务域内可处理的会话 Cookie 转为本机保留 30 天，由 Chromium 写回原 Cookie 存储。已有明确过期时间的 Cookie 不延长，Host-only、HttpOnly、Secure、SameSite 和可识别的分区属性保持。
4. 面板关闭环境前再次同步，并等待该环境的浏览器进程正常退出。下次启动同一目录时，浏览器直接加载保存的数据，面板再检查实际登录状态。

不会导出或跨环境注入 Cookie，不保存另一份可被恢复的旧登录快照。用户在平台主动退出后，不会用旧快照重新登录。Local Storage、IndexedDB 等继续由原 Profile 保存。`.browser-workbench-cookie-sync.json` 仅记录同步状态、时间和数量，不包含 Cookie 名称、值或密码。

“已保存”表示本机数据同步完成，不等于“已登录”，也不是 30 天免登录承诺。平台撤销会话、令牌过期、要求验证码或新的设备验证时，仍需人工处理。断电、浏览器崩溃或直接关窗口发生在最近一次同步前，也可能丢失刚创建的会话。

## 验证

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
node backend/test_auth_scripts.mjs
npm run build
```

HTTP 服务只监听 `127.0.0.1`；动作需要本机来源及当前请求令牌，API 只接受登记的环境 ID 和固定动作，不执行任意命令。正常关闭针对归属校验后的单个浏览器，通过 CDP 发出关闭，不使用全局 `taskkill`。详情中的本地 CDP 地址用于本机连接，不应配置为公网服务。
