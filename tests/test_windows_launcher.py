import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_POWERSHELL = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32/WindowsPowerShell/v1.0/powershell.exe"
)


@unittest.skipUnless(
    os.name == "nt" and WINDOWS_POWERSHELL.is_file(),
    "Windows PowerShell launcher integration tests",
)
class WindowsLauncherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="workbench launcher ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "项目 with spaces"
        self.project.mkdir()
        for name in ("Install-Local.ps1", "Start-Workbench.ps1"):
            shutil.copyfile(PROJECT_ROOT / name, self.project / name)

        self.private_root = self.root / "私有 data"
        self.private_root.mkdir()
        self.registry = self.private_root / "shops.json"
        self.registry.write_text(
            json.dumps({"schema_version": 1, "shops": []}), encoding="utf-8"
        )
        self.install_appdata = self.root / "installer AppData"
        self.launch_appdata = self.root / "desktop AppData"
        self.install_appdata.mkdir()
        self.launch_appdata.mkdir()
        self.pointer = self.project / "runtime/settings-path.txt"

        # Return an existing, matching service so these tests never start a
        # backend or open a browser. Record the requested URL independently of
        # the expected port, so incorrect settings produce assertion failures.
        self.health_wrapper = self.root / "launch with health.ps1"
        self.health_wrapper.write_text(
            r"""param(
    [string]$Launcher,
    [string]$ExpectedProjectRoot,
    [string]$SettingsPath = ''
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
# Keep launcher failures noninteractive, including failures in the health stub.
Microsoft.PowerShell.Utility\Add-Type -TypeDefinition @'
namespace System.Windows.Forms {
    public static class MessageBox {
        public static void Show(string message, string title) {
            throw new System.InvalidOperationException(message);
        }
    }
}
'@
function Add-Type {
    param([string]$AssemblyName)
    if ($AssemblyName -ne 'System.Windows.Forms') {
        throw "Unexpected assembly requested by launcher: $AssemblyName"
    }
}
$global:healthRequests = New-Object 'System.Collections.Generic.List[string]'
$global:healthProjectRoot = $ExpectedProjectRoot
function Invoke-RestMethod {
    param([string]$Uri, [int]$TimeoutSec)
    $global:healthRequests.Add($Uri)
    return [pscustomobject]@{
        app = 'browser-workbench'
        project_root = $global:healthProjectRoot
    }
}
if ($SettingsPath) {
    $launchOutput = @(& $Launcher -SettingsPath $SettingsPath -NoOpen)
} else {
    $launchOutput = @(& $Launcher -NoOpen)
}
if ($LASTEXITCODE) { exit $LASTEXITCODE }
@{
    output = $launchOutput
    health_requests = @($global:healthRequests.ToArray())
} | ConvertTo-Json -Compress
""",
            encoding="utf-8-sig",
        )

    def run_powershell(self, script, arguments, *, local_appdata, expect_success=True):
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = str(local_appdata)
        result = subprocess.run(
            [
                str(WINDOWS_POWERSHELL),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                *map(str, arguments),
            ],
            cwd=self.project,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        if expect_success:
            self.assertEqual(
                result.returncode,
                0,
                f"PowerShell failed:\n{result.stdout}\n{result.stderr}",
            )
            return result.stdout.strip()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        return result.stderr

    def install(self, port, runtime_root=None):
        arguments = [
            "-Registry",
            self.registry,
            "-Python",
            sys.executable,
            "-Port",
            port,
            "-NoShortcut",
        ]
        if runtime_root is not None:
            arguments.extend(["-RuntimeRoot", runtime_root])
        self.run_powershell(
            self.project / "Install-Local.ps1",
            arguments,
            local_appdata=self.install_appdata,
        )

    def write_settings(self, path, port):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "registry": str(self.registry),
                    "python": sys.executable,
                    "port": port,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8-sig",
        )
        return path

    def write_pointer(self, path):
        self.pointer.parent.mkdir(parents=True, exist_ok=True)
        self.pointer.write_text(str(path) + "\n", encoding="utf-8-sig")

    def assert_installed_settings(self, path, port):
        settings = json.loads(path.read_text(encoding="utf-8-sig"))
        self.assertEqual(Path(settings["registry"]), self.registry)
        self.assertTrue(Path(settings["registry"]).is_absolute())
        self.assertEqual(Path(settings["python"]), Path(sys.executable))
        self.assertTrue(Path(settings["python"]).is_absolute())
        self.assertEqual(settings["port"], port)
        self.assertEqual(
            Path(self.pointer.read_text(encoding="utf-8-sig").strip()), path
        )

    def assert_launches_on(self, port, settings_path=None):
        arguments = [
            "-Launcher",
            self.project / "Start-Workbench.ps1",
            "-ExpectedProjectRoot",
            self.project,
        ]
        if settings_path is not None:
            arguments.extend(["-SettingsPath", settings_path])
        output = self.run_powershell(
            self.health_wrapper,
            arguments,
            local_appdata=self.launch_appdata,
        )
        result = json.loads(output)
        panel_url = f"http://127.0.0.1:{port}"
        self.assertEqual(result["output"], [panel_url])
        self.assertEqual(result["health_requests"], [f"{panel_url}/api/health"])

    def test_install_defaults_to_runtime_beside_registry(self):
        self.install(54361)

        self.assert_installed_settings(
            self.private_root / "runtime/settings.json", 54361
        )
        self.assertEqual(list(self.install_appdata.iterdir()), [])

    def test_install_honors_explicit_runtime_root(self):
        runtime_root = self.root / "独立 runtime with spaces"
        self.install(54362, runtime_root)

        self.assert_installed_settings(runtime_root / "settings.json", 54362)
        self.assertFalse((self.private_root / "runtime").exists())
        self.assertEqual(list(self.install_appdata.iterdir()), [])

    def test_start_uses_install_pointer_when_local_appdata_changes(self):
        self.install(54363)
        self.write_settings(
            self.launch_appdata / "BrowserWorkbench/settings.json", 54364
        )

        self.assert_launches_on(54363)

    def test_explicit_settings_path_overrides_pointer_and_legacy_config(self):
        explicit_settings = self.write_settings(
            self.root / "显式 config/settings.json", 54365
        )
        pointer_settings = self.write_settings(
            self.private_root / "runtime/settings.json", 54366
        )
        self.write_settings(
            self.launch_appdata / "BrowserWorkbench/settings.json", 54367
        )

        for pointer_target in (
            pointer_settings,
            self.root / "missing config/settings.json",
        ):
            with self.subTest(pointer_target=pointer_target.name):
                self.write_pointer(pointer_target)
                self.assert_launches_on(54365, explicit_settings)

    def test_start_falls_back_to_legacy_config_without_pointer(self):
        self.write_settings(
            self.launch_appdata / "BrowserWorkbench/settings.json", 54368
        )

        self.assert_launches_on(54368)

    def test_stale_pointer_reports_missing_config_without_legacy_fallback(self):
        self.write_pointer(self.private_root / "missing-settings.json")
        self.write_settings(
            self.launch_appdata / "BrowserWorkbench/settings.json", 54369
        )

        error = self.run_powershell(
            self.health_wrapper,
            [
                "-Launcher",
                self.project / "Start-Workbench.ps1",
                "-ExpectedProjectRoot",
                self.project,
            ],
            local_appdata=self.launch_appdata,
            expect_success=False,
        )

        self.assertIn("missing-settings.json", error)


if __name__ == "__main__":
    unittest.main()
