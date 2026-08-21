[CmdletBinding()]
param(
    [string]$Python = ".venv\Scripts\python.exe",
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [switch]$SkipPrivacyScan,
    [switch]$HeadlessSmoke
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = if ([IO.Path]::IsPathRooted($Python)) {
    $Python
} else {
    Join-Path $ProjectRoot $Python
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory)] [string]$FilePath,
        [Parameter(ValueFromRemainingArguments)] [string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $FilePath $Arguments"
    }
}

function Find-InnoCompiler {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }
    throw "Inno Setup 6 compiler (ISCC.exe) was not found."
}

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python executable not found: $PythonPath"
}

Push-Location $ProjectRoot
try {
    $VersionOutput = & $PythonPath "build\check_version.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not determine a valid release version."
    }
    $Version = [string]($VersionOutput | Select-Object -Last 1)
    $Version = $Version.Trim()
    if ($Version -notmatch '^\d+\.\d+\.\d+$') {
        throw "Could not determine a valid release version."
    }

    if (-not $SkipTests) {
        Invoke-NativeCommand $PythonPath -m unittest discover -s tests -v
    }
    if (-not $SkipPrivacyScan) {
        if (-not (Test-Path -LiteralPath "scripts\privacy_scan.py" -PathType Leaf)) {
            throw "scripts/privacy_scan.py is required for a release build."
        }
        Invoke-NativeCommand $PythonPath scripts\privacy_scan.py --repo-root .
    }

    Invoke-NativeCommand $PythonPath -m PyInstaller --noconfirm --clean `
        --distpath dist --workpath build\pyinstaller build\fmost_brain_viewer.spec

    $AppDirectory = Join-Path $ProjectRoot "dist\fMOST Brain Viewer"
    $AppExecutable = Join-Path $AppDirectory "fMOST Brain Viewer.exe"
    if (-not (Test-Path -LiteralPath $AppExecutable -PathType Leaf)) {
        throw "PyInstaller output is missing: $AppExecutable"
    }
    $SelfTestArgument = if ($HeadlessSmoke) { "--ci-smoke-test" } else { "--self-test" }
    Invoke-NativeCommand $AppExecutable $SelfTestArgument

    $ReleaseDirectory = Join-Path $ProjectRoot "release"
    New-Item -ItemType Directory -Force -Path $ReleaseDirectory | Out-Null
    $PortableName = "fMOST-Brain-Viewer-Portable-$Version-win64.zip"
    $PortablePath = Join-Path $ReleaseDirectory $PortableName
    if (Test-Path -LiteralPath $PortablePath) {
        Remove-Item -LiteralPath $PortablePath -Force
    }
    Compress-Archive -Path (Join-Path $AppDirectory "*") -DestinationPath $PortablePath `
        -CompressionLevel Optimal

    $InstallerPath = $null
    if (-not $SkipInstaller) {
        $InnoCompiler = Find-InnoCompiler
        Invoke-NativeCommand $InnoCompiler "/DMyAppVersion=$Version" `
            "installer\fmost_brain_viewer.iss"
        $InstallerPath = Join-Path $ReleaseDirectory `
            "fMOST-Brain-Viewer-Setup-$Version-win64.exe"
        if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
            throw "Inno Setup output is missing: $InstallerPath"
        }
    }

    if (-not $SkipPrivacyScan) {
        $PrivacyArguments = @("scripts\privacy_scan.py", "--repo-root", ".", `
            "--artifact", $PortablePath)
        if ($InstallerPath) {
            $PrivacyArguments += @("--artifact", $InstallerPath)
        }
        Invoke-NativeCommand $PythonPath @PrivacyArguments
    }

    $Artifacts = @($PortablePath)
    if ($InstallerPath) {
        $Artifacts += $InstallerPath
    }
    $ChecksumLines = foreach ($Artifact in $Artifacts | Sort-Object) {
        $Hash = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash.ToLowerInvariant()
        "$Hash  $([IO.Path]::GetFileName($Artifact))"
    }
    $ChecksumPath = Join-Path $ReleaseDirectory "SHA256SUMS.txt"
    [IO.File]::WriteAllLines($ChecksumPath, $ChecksumLines, `
        [Text.UTF8Encoding]::new($false))

    Write-Host "Release $Version created in $ReleaseDirectory"
    Get-ChildItem -LiteralPath $ReleaseDirectory -File | `
        Where-Object Name -In @($PortableName, `
            "fMOST-Brain-Viewer-Setup-$Version-win64.exe", "SHA256SUMS.txt") | `
        Select-Object Name, Length, LastWriteTime
} finally {
    Pop-Location
}
