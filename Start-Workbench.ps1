param([string]$SettingsPath = '', [switch]$NoOpen)
$ErrorActionPreference = 'Stop'
$launchMutex = $null
$lockTaken = $false
try {
    if (-not $SettingsPath) {
        $settingsPointer = Join-Path $PSScriptRoot 'runtime\settings-path.txt'
        if (Test-Path -LiteralPath $settingsPointer) {
            $SettingsPath = (Get-Content -LiteralPath $settingsPointer -Raw -Encoding UTF8).Trim()
        } else {
            $SettingsPath = Join-Path $env:LOCALAPPDATA 'BrowserWorkbench\settings.json'
        }
    }
    if (-not $SettingsPath -or -not (Test-Path -LiteralPath $SettingsPath)) { throw "找不到本机配置：$SettingsPath`n请先运行 Install-Local.ps1 配置浏览器登记文件。" }
    $settings = Get-Content -LiteralPath $SettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $projectRoot = $PSScriptRoot
    $panelPort = [int]$settings.port
    if ($panelPort -lt 1024 -or $panelPort -gt 65535) { throw '面板端口无效。' }
    $launchMutex = New-Object System.Threading.Mutex($false, "Local\BrowserWorkbench-$panelPort")
    try { $lockTaken = $launchMutex.WaitOne(20000) } catch [System.Threading.AbandonedMutexException] { $lockTaken = $true }
    if (-not $lockTaken) { throw '另一次启动仍在进行，请稍后再试。' }
    $panelUrl = "http://127.0.0.1:$panelPort"
    $health = $null
    try { $health = Invoke-RestMethod "$panelUrl/api/health" -TimeoutSec 2 } catch {}
    if ($health -and ($health.app -ne 'browser-workbench' -or $health.project_root -ne $projectRoot)) {
        throw '面板端口已被其他项目占用，请修改本地 settings.json 的 port。'
    }
    if (-not $health) {
        if (-not (Test-Path -LiteralPath "$projectRoot\dist\index.html")) { throw '缺少已构建页面，请先运行 npm install 和 npm run build。' }
        if (-not (Test-Path -LiteralPath $settings.registry)) { throw '浏览器登记文件不存在。' }
        $runtimeRoot = Split-Path -Parent $SettingsPath
        $arguments = @('-m', 'backend.server', '--registry', ('"' + $settings.registry + '"'), '--port', $panelPort)
        $process = Start-Process -FilePath $settings.python -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput "$runtimeRoot\server.log" -RedirectStandardError "$runtimeRoot\server-error.log" -PassThru
        $ready = $false
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            try {
                $health = Invoke-RestMethod "$panelUrl/api/health" -TimeoutSec 1
                if ($health.app -eq 'browser-workbench' -and $health.project_root -eq $projectRoot) { $ready = $true; break }
            } catch {}
            if ($process.HasExited) { throw "面板服务启动失败，请查看 $runtimeRoot\server-error.log" }
            Start-Sleep -Milliseconds 400
        }
        if (-not $ready) { throw '面板服务启动超时，请查看本地服务日志。' }
    }
    if (-not $NoOpen) { Start-Process $panelUrl }
    Write-Output $panelUrl
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '浏览器工作台') | Out-Null
    exit 1
} finally {
    if ($launchMutex) {
        if ($lockTaken) { $launchMutex.ReleaseMutex() }
        $launchMutex.Dispose()
    }
}
