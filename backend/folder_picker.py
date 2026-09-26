"""User-invoked Windows folder picker; no browser or filesystem mutations."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading


_picker_lock = threading.Lock()
PICKER_TIMEOUT = 170
PICKER_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$picker = New-Object System.Windows.Forms.FolderBrowserDialog
$owner = New-Object System.Windows.Forms.Form
try {
    $picker.Description = '选择新店铺浏览器环境的存放文件夹'
    $picker.ShowNewFolderButton = $true
    if ([System.IO.Directory]::Exists($env:BROWSER_WORKBENCH_PICKER_START)) {
        $picker.SelectedPath = $env:BROWSER_WORKBENCH_PICKER_START
    }
    $owner.ShowInTaskbar = $false
    $owner.TopMost = $true
    $owner.Opacity = 0
    $owner.Width = 1
    $owner.Height = 1
    $owner.Show()
    $result = $picker.ShowDialog($owner)
    if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
        @{path=$picker.SelectedPath;cancelled=$false} | ConvertTo-Json -Compress
    } else {
        @{path=$null;cancelled=$true} | ConvertTo-Json -Compress
    }
} finally {
    $picker.Dispose()
    $owner.Dispose()
}
"""


class FolderPickerError(RuntimeError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def choose_folder(initial_path: str) -> dict:
    if os.name != "nt":
        raise FolderPickerError("文件夹选择器目前需要 Windows，也可直接填写绝对路径", "folder_picker_unavailable")
    if not _picker_lock.acquire(blocking=False):
        raise FolderPickerError("文件夹选择窗口已经打开，请先完成或取消选择", "folder_picker_busy")
    try:
        environment = {**os.environ, "BROWSER_WORKBENCH_PICKER_START": initial_path}
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-STA", "-Command", PICKER_SCRIPT],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
            timeout=PICKER_TIMEOUT, env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        value = json.loads(result.stdout.strip())
        if not isinstance(value, dict) or type(value.get("cancelled")) is not bool:
            raise ValueError("invalid picker response")
        if value["cancelled"]:
            return {"path": None, "cancelled": True}
        if not isinstance(value.get("path"), str) or not value["path"].strip():
            raise ValueError("missing folder")
        return {"path": value["path"], "cancelled": False}
    except subprocess.TimeoutExpired as exc:
        raise FolderPickerError("文件夹选择已超时，请重新选择或直接填写路径", "folder_picker_timeout") from exc
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise FolderPickerError("无法打开文件夹选择器，可直接填写存放位置的绝对路径", "folder_picker_unavailable") from exc
    finally:
        _picker_lock.release()
