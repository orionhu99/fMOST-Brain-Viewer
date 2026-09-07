[CmdletBinding()]
param([Parameter(Mandatory)] [string]$Installer, [switch]$HeadlessSmoke)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$InstallerPath = (Resolve-Path -LiteralPath $Installer).Path
if ([IO.Path]::GetFileName($InstallerPath) -notlike 'fMOST-Brain-Viewer-Installer-Test-*') {
    throw 'Use the dedicated /DTestInstall build to avoid modifying production registration.'
}
$TemporaryRoot = [IO.Path]::GetFullPath($(if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP })).TrimEnd('\')
$TestDirectory = [IO.Path]::GetFullPath((Join-Path $TemporaryRoot ('fMOST 安装测试 ' + [guid]::NewGuid().ToString('N'))))
if (-not $TestDirectory.StartsWith($TemporaryRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $TestDirectory)) {
    throw 'Unsafe or already existing test directory.'
}
New-Item -ItemType Directory -Path $TestDirectory | Out-Null
$Marker = Join-Path $TestDirectory '.viewer-install-test'
[IO.File]::WriteAllText($Marker, 'isolated installer fixture')
$SelfTestArgument = if ($HeadlessSmoke) { '--ci-smoke-test' } else { '--self-test' }
function Run-And-Check {
    param([string]$FilePath, [string[]]$Arguments)
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    $deadline = [DateTime]::UtcNow.AddMinutes(5)
    while (-not $process.WaitForExit(15000)) {
        if ([DateTime]::UtcNow -ge $deadline) {
            Stop-Process -Id $process.Id -Force
            throw "Installer test timed out: $FilePath"
        }
        Write-Host "Waiting for installer test process $($process.Id)..."
    }
    if ($process.ExitCode -ne 0) { throw "Test failed with $($process.ExitCode): $FilePath" }
}
try {
    $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/DIR=`"$TestDirectory`"")
    Run-And-Check $InstallerPath $arguments
    $Executable = Join-Path $TestDirectory 'fMOST Brain Viewer.exe'
    Run-And-Check $Executable @($SelfTestArgument)
    $UserFolder = Join-Path $TestDirectory 'user analysis'
    New-Item -ItemType Directory -Path $UserFolder | Out-Null
    $Sentinel = Join-Path $UserFolder 'saved-session.txt'
    [IO.File]::WriteAllText($Sentinel, 'must survive upgrade and uninstall')
    $Expected = (Get-FileHash -LiteralPath $Sentinel).Hash
    Run-And-Check $InstallerPath $arguments
    if ((Get-FileHash -LiteralPath $Sentinel).Hash -ne $Expected) { throw 'Upgrade changed a user file.' }
    Run-And-Check $Executable @($SelfTestArgument)
    Run-And-Check (Join-Path $TestDirectory 'unins000.exe') @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
    if (Test-Path -LiteralPath $Executable) { throw 'Application remains after uninstall.' }
    if ((Get-FileHash -LiteralPath $Sentinel).Hash -ne $Expected) { throw 'Uninstall changed a user file.' }
    Write-Host 'PASS: Unicode-path install, upgrade, self-tests and uninstall preserve user files.'
} finally {
    # Only clean the unique fixture made by this invocation, after checking its boundary.
    if ((Test-Path -LiteralPath $Marker) -and $TestDirectory.StartsWith($TemporaryRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $TestDirectory -Recurse -Force
    }
}
