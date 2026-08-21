[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Installer,
    [string]$InstallDirectory,
    [switch]$HeadlessSmoke
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$TemporaryRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
if (-not $InstallDirectory) {
    $InstallDirectory = Join-Path $TemporaryRoot "fMOST Brain Viewer 安装测试"
}
$InstallerPath = (Resolve-Path -LiteralPath $Installer).Path
New-Item -ItemType Directory -Force -Path $InstallDirectory | Out-Null
$SelfTestArgument = if ($HeadlessSmoke) { "--ci-smoke-test" } else { "--self-test" }

function Run-And-Check {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [int]$TimeoutMinutes = 15
    )
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -PassThru
    $deadline = [DateTime]::UtcNow.AddMinutes($TimeoutMinutes)
    while (-not $process.HasExited) {
        if ([DateTime]::UtcNow -ge $deadline) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "$FilePath exceeded the $TimeoutMinutes minute timeout."
        }
        Write-Host "Waiting for $([IO.Path]::GetFileName($FilePath)) (PID $($process.Id))..."
        $null = $process.WaitForExit(15000)
        $process.Refresh()
    }
    if ($process.ExitCode -ne 0) {
        throw "$FilePath exited with code $($process.ExitCode)."
    }
}

try {
    # First install validates a standard-user path containing spaces and Unicode.
    Run-And-Check $InstallerPath @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
        "/DIR=`"$InstallDirectory`""
    )
    $Executable = Join-Path $InstallDirectory "fMOST Brain Viewer.exe"
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "Installed executable not found: $Executable"
    }
    Run-And-Check $Executable @($SelfTestArgument)

    # A second install exercises the fixed AppId in-place upgrade path.
    Run-And-Check $InstallerPath @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
        "/DIR=`"$InstallDirectory`""
    )
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "The executable is missing after in-place upgrade: $Executable"
    }
    Run-And-Check $Executable @($SelfTestArgument)

    $Uninstaller = Join-Path $InstallDirectory "unins000.exe"
    if (-not (Test-Path -LiteralPath $Uninstaller -PathType Leaf)) {
        throw "Uninstaller not found: $Uninstaller"
    }
    Run-And-Check $Uninstaller @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
    if (Test-Path -LiteralPath $Executable) {
        throw "The executable remains after uninstall: $Executable"
    }
    Write-Host "Unicode-path install, in-place upgrade, frozen self-tests, and uninstall passed."
} finally {
    if (Test-Path -LiteralPath $InstallDirectory) {
        Remove-Item -LiteralPath $InstallDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}
