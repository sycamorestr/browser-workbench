param(
    [Parameter(Mandatory=$true)][string]$Registry,
    [string]$Python = 'python',
    [int]$Port = 17860,
    [switch]$NoShortcut
)
$ErrorActionPreference = 'Stop'
$registryPath = (Resolve-Path -LiteralPath $Registry).Path
$pythonPath = (Get-Command $Python -ErrorAction Stop).Source
if ($Port -lt 1024 -or $Port -gt 65535) { throw '端口必须在 1024 至 65535 之间。' }
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'BrowserWorkbench'
New-Item -Path $runtimeRoot -ItemType Directory -Force | Out-Null
@{ registry = $registryPath; python = $pythonPath; port = $Port } | ConvertTo-Json | Set-Content -LiteralPath "$runtimeRoot\settings.json" -Encoding UTF8
if (-not $NoShortcut) {
    $desktopRoot = [Environment]::GetFolderPath('Desktop')
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $desktopRoot '浏览器工作台.lnk'))
    $shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $PSScriptRoot + '\Start-Workbench.ps1"'
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.Description = '管理店铺浏览器、登录状态与 Playwright CDP 连接'
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,20"
    $shortcut.Save()
}
Write-Output '本地配置已保存。运行 Start-Workbench.ps1 或双击桌面的“浏览器工作台”。'
