[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Wbfs,

    [string]$CpuRuntime = $env:OPENSPM_CPU_RUNTIME,
    [string]$Wit = $env:OPENSPM_WIT,
    [string]$DolRecomp = $env:OPENSPM_DOLRECOMP,
    [string]$GeneratedDirect,

    [ValidateSet("AUTO", "D3D12", "D3D11", "VULKAN", "WEBGPU")]
    [string]$Graphics = "AUTO",

    [switch]$NoLaunch,
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Cache = Join-Path $Root ".openspm\m14"
$Candidate = Join-Path $Cache "windows-candidate"
$GameDir = Join-Path $Cache "game"
$GeneratedStage = Join-Path $Cache "stage\generated-direct"
$AuroraDir = Join-Path $Cache "deps\aurora"
$AuroraOwnerMarker = Join-Path $Cache "deps\aurora.openspm-owned"
$LocalModuleBuild = Join-Path $Cache "local-module-build"
$HomeLogDir = Join-Path $Cache "home-test"
$LaunchLog = Join-Path $HomeLogDir "launch.log"
$SummaryFile = Join-Path $HomeLogDir "last-run.txt"
$PinnedAurora = "749d6ee7a22bdfab78c8ece9047bca5d79aa72ca"
$AuroraRepo = "https://github.com/encounter/aurora.git"

New-Item -ItemType Directory -Force -Path $Cache, (Join-Path $Cache "deps"), $HomeLogDir | Out-Null

function Quote-Display([string]$Value) {
    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '""') + '"'
    }
    return $Value
}

function Invoke-Native([string]$File, [string[]]$Arguments) {
    $shown = @((Quote-Display $File)) + @($Arguments | ForEach-Object { Quote-Display $_ })
    Write-Host ("> " + ($shown -join " ")) -ForegroundColor Cyan
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $File"
    }
}

function Find-CommandPath([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $cmd) { return $null }
    if ($cmd.PSObject.Properties.Name -contains "Source" -and $cmd.Source) { return $cmd.Source }
    if ($cmd.PSObject.Properties.Name -contains "Path" -and $cmd.Path) { return $cmd.Path }
    return $cmd.Name
}

function Resolve-ExistingFile([string]$Value, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    $trimmed = $Value.Trim().Trim('"')
    if (-not (Test-Path -LiteralPath $trimmed -PathType Leaf)) {
        throw "$Label does not exist: $trimmed"
    }
    return (Resolve-Path -LiteralPath $trimmed).Path
}

function Resolve-ExistingDirectory([string]$Value, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    $trimmed = $Value.Trim().Trim('"')
    if (-not (Test-Path -LiteralPath $trimmed -PathType Container)) {
        throw "$Label does not exist: $trimmed"
    }
    return (Resolve-Path -LiteralPath $trimmed).Path
}

function Resolve-Tool([string]$Explicit, [string]$FallbackName, [string]$Label) {
    if (-not [string]::IsNullOrWhiteSpace($Explicit)) {
        return Resolve-ExistingFile $Explicit $Label
    }
    $found = Find-CommandPath $FallbackName
    if ($found) { return $found }
    return $null
}

function Invoke-Python([string[]]$Arguments) {
    Invoke-Native $script:PythonExe ($script:PythonPrefix + $Arguments)
}

Write-Host "OpenSPM M14 home test" -ForegroundColor Green
Write-Host "This path is strict-native: no Dolphin/ModernGekko fallback and no diagnostic input probes." -ForegroundColor DarkGray

$Git = Find-CommandPath "git"
$CMake = Find-CommandPath "cmake"
$Ninja = Find-CommandPath "ninja"
$CTest = Find-CommandPath "ctest"
$PythonExe = Find-CommandPath "python"
$PythonPrefix = @()
if (-not $PythonExe) {
    $PythonExe = Find-CommandPath "py"
    if ($PythonExe) { $PythonPrefix = @("-3") }
}

$missing = @()
if (-not $Git) { $missing += "git" }
if (-not $CMake) { $missing += "cmake" }
if (-not $Ninja) { $missing += "ninja" }
if (-not $CTest) { $missing += "ctest" }
if (-not $PythonExe) { $missing += "Python 3" }
if ($missing.Count -gt 0) {
    throw "Missing required build tools: $($missing -join ', '). Install/enable them, then rerun this script."
}

if (-not (Find-CommandPath "cl")) {
    throw "MSVC cl.exe is not active. Run RUN-M14-HOME-TEST.cmd instead (it enters the latest Visual Studio x64 developer environment automatically), or run this script from an x64 Native Tools/Developer PowerShell."
}

$branch = (& $Git -C $Root rev-parse --abbrev-ref HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not read the current git branch." }
if ($branch -ne "m14-native-execution") {
    throw "Wrong branch '$branch'. Checkout m14-native-execution before running the M14 home test."
}

if ([string]::IsNullOrWhiteSpace($Wbfs)) {
    $Wbfs = Read-Host "Path to your Super Paper Mario WBFS"
}
$Wbfs = Resolve-ExistingFile $Wbfs "WBFS"

$CpuRuntime = Resolve-ExistingDirectory $CpuRuntime "DolRecomp CPU runtime"
if (-not $CpuRuntime) {
    $CpuRuntime = Resolve-ExistingDirectory (Read-Host "Path to the DolRecomp CPU runtime root (the folder containing cpu\cpu.c)") "DolRecomp CPU runtime"
}
if (-not (Test-Path -LiteralPath (Join-Path $CpuRuntime "cpu\cpu.c") -PathType Leaf)) {
    throw "CPU runtime root must contain cpu\cpu.c: $CpuRuntime"
}

$WitExe = Resolve-Tool $Wit "wit" "wit"
if (-not $WitExe) {
    $WitExe = Resolve-Tool (Read-Host "wit.exe was not on PATH. Path to wit.exe") "wit" "wit"
}
if (-not $WitExe) { throw "wit.exe is required to extract the player-owned WBFS." }

$GeneratedDirectResolved = $null
if (-not [string]::IsNullOrWhiteSpace($GeneratedDirect)) {
    $GeneratedDirectResolved = Resolve-ExistingDirectory $GeneratedDirect "direct generated source"
    if (-not (Test-Path -LiteralPath (Join-Path $GeneratedDirectResolved "generated.h") -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $GeneratedDirectResolved "chunks") -PathType Container)) {
        throw "-GeneratedDirect must contain generated.h and chunks/."
    }
}

$DolRecompExe = $null
if (-not $GeneratedDirectResolved) {
    $DolRecompExe = Resolve-Tool $DolRecomp "dolrecomp" "dolrecomp"
    if (-not $DolRecompExe) {
        $DolRecompExe = Resolve-Tool (Read-Host "dolrecomp.exe was not on PATH. Path to dolrecomp.exe (or rerun with -GeneratedDirect)") "dolrecomp" "dolrecomp"
    }
    if (-not $DolRecompExe) { throw "dolrecomp.exe is required when -GeneratedDirect is not supplied." }
}

Write-Host "`n[1/6] Preparing the pinned Aurora source" -ForegroundColor Yellow
if (Test-Path -LiteralPath $AuroraDir) {
    if (-not (Test-Path -LiteralPath $AuroraOwnerMarker -PathType Leaf)) {
        throw "Refusing to reset existing unowned Aurora directory: $AuroraDir"
    }
    Invoke-Native $Git @("-C", $AuroraDir, "reset", "--hard")
    Invoke-Native $Git @("-C", $AuroraDir, "clean", "-fdx")
    $auroraHead = (& $Git -C $AuroraDir rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read cached Aurora HEAD: $AuroraDir"
    }
    if ($auroraHead -ne $PinnedAurora) {
        Invoke-Native $Git @("-C", $AuroraDir, "fetch", "--depth", "1", "origin", $PinnedAurora)
    }
    Invoke-Native $Git @("-C", $AuroraDir, "checkout", "--detach", $PinnedAurora)
} else {
    New-Item -ItemType Directory -Force -Path $AuroraDir | Out-Null
    # From this point onward the directory is definitely script-owned. Mark it
    # before network work so a failed first fetch remains safely recoverable on
    # the next run instead of becoming an unowned half-initialized checkout.
    Set-Content -LiteralPath $AuroraOwnerMarker -Encoding ASCII -Value "OpenSPM-owned pinned Aurora cache. Safe for RUN-M14-HOME-TEST.ps1 to reset."
    Invoke-Native $Git @("-C", $AuroraDir, "init")
    Invoke-Native $Git @("-C", $AuroraDir, "remote", "add", "origin", $AuroraRepo)
    Invoke-Native $Git @("-C", $AuroraDir, "fetch", "--depth", "1", "origin", $PinnedAurora)
    Invoke-Native $Git @("-C", $AuroraDir, "checkout", "--detach", $PinnedAurora)
}
Invoke-Python @((Join-Path $Root "renderer\aurora\apply_patches.py"), $AuroraDir)
Invoke-Python @((Join-Path $Root "renderer\aurora\apply_raster_patch.py"), $AuroraDir)

Write-Host "`n[2/6] Building the strict native launcher/backend/Aurora renderer" -ForegroundColor Yellow
Invoke-Native $CMake @(
    "-S", $Root,
    "-B", $Candidate,
    "-G", "Ninja",
    "-DCMAKE_BUILD_TYPE=Release",
    "-DOPENSPM_BUILD_AURORA_RENDERER=ON",
    "-DOPENSPM_AURORA_SOURCE_DIR=$AuroraDir",
    "-DAURORA_DAWN_PROVIDER=package",
    "-DAURORA_SDL3_PROVIDER=package"
)

$buildTargets = @("OpenSPMNative", "openspm_native_backend", "openspm_renderer")
if (-not $SkipTests) {
    # Build exactly the OpenSPM contract-test payload instead of the whole
    # Aurora subproject. A targeted list keeps the home path quick and avoids
    # pulling unrelated third-party examples/tests into the proof.
    $buildTargets += @(
        "m13_abi_test",
        "m14_native_runtime_test",
        "m14_synthetic_native_module",
        "m14_native_loader_test",
        "m14_dol_image_test",
        "m14_disc_boot_test",
        "m14_wii_platform_test",
        "m14_wii_boot_platform_test",
        "m14_gx_platform_test",
        "m14_gx_display_list_test",
        "m14_sha256_test",
        "moderngekko_backend",
        "m13_backend_query_test",
        "m13_dll_query_test",
        "m14_gx_renderer_service_test",
        "m14_synthetic_renderer",
        "m14_renderer_loader_test",
        "openspm_debug",
        "m13_mod_loader_test",
        "m13_config_test"
    )
}
Invoke-Native $CMake (@("--build", $Candidate, "--target") + $buildTargets + @("-j2"))

if (-not $SkipTests) {
    Write-Host "`n[3/6] Running OpenSPM contract tests" -ForegroundColor Yellow
    Invoke-Native $CTest @("--test-dir", $Candidate, "--output-on-failure", "-R", "^(m13_|m14_)")
} else {
    Write-Host "`n[3/6] Contract tests skipped by request" -ForegroundColor DarkYellow
}

Write-Host "`n[4/6] Verifying/extracting WBFS and staging direct native source" -ForegroundColor Yellow
$resumeArgs = @(
    (Join-Path $Root "tools\m14_lab\resume_m14.py"),
    "--wbfs", $Wbfs,
    "--wit", $WitExe,
    "--probe", "none",
    "--no-build"
)
if ($GeneratedDirectResolved) {
    $resumeArgs += @("--generated", $GeneratedDirectResolved, "--generated-is-direct")
} else {
    $resumeArgs += @("--dolrecomp", $DolRecompExe)
}
Invoke-Python $resumeArgs

if (-not (Test-Path -LiteralPath (Join-Path $GameDir "sys\main.dol") -PathType Leaf)) {
    throw "WBFS preparation completed without the expected extracted sys\main.dol."
}
if (-not (Test-Path -LiteralPath (Join-Path $GeneratedStage "generated.h") -PathType Leaf)) {
    throw "Native source staging completed without generated.h."
}

Write-Host "`n[5/6] Building the player-local native SPM module" -ForegroundColor Yellow
$OutputDir = Join-Path $Candidate "User-OpenSPM\native"
Invoke-Python @(
    (Join-Path $Root "tools\native_bridge\build_local_module.py"),
    "--openspm-root", $Root,
    "--generated", $GeneratedStage,
    "--cpu-runtime", $CpuRuntime,
    "--main-dol", (Join-Path $GameDir "sys\main.dol"),
    "--build-dir", $LocalModuleBuild,
    "--output-dir", $OutputDir,
    "--generator", "Ninja",
    "--skip-direct-calls"
)

$Launcher = Join-Path $Candidate "OpenSPMNative.exe"
$Backend = Join-Path $Candidate "runtime\openspm_native_backend.dll"
$Renderer = Join-Path $Candidate "runtime\openspm_renderer.dll"
$LocalModule = Join-Path $OutputDir "openspm_local_native.dll"
foreach ($required in @($Launcher, $Backend, $Renderer, $LocalModule)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Home-test candidate is incomplete; missing: $required"
    }
}

$runtimeDllCount = @(Get-ChildItem -LiteralPath (Join-Path $Candidate "runtime") -Filter "*.dll" -File).Count
$manifestLines = @(
    "OpenSPM M14 home-test candidate",
    "branch=$branch",
    "graphics=$Graphics",
    "diagnostic_probe=none",
    "game=$GameDir",
    "launcher=$Launcher",
    "backend=$Backend",
    "renderer=$Renderer",
    "local_module=$LocalModule",
    "runtime_dll_count=$runtimeDllCount",
    "",
    "SHA256:"
)
foreach ($path in @($Launcher, $Backend, $Renderer, $LocalModule)) {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    $manifestLines += "$hash  $path"
}
$manifestLines | Set-Content -LiteralPath $SummaryFile -Encoding UTF8

Write-Host "`n[6/6] Candidate ready" -ForegroundColor Green
Write-Host "Launcher:     $Launcher"
Write-Host "Game:         $GameDir"
Write-Host "Local module: $LocalModule"
Write-Host "Runtime DLLs: $runtimeDllCount"
Write-Host "Probe:        none"
Write-Host "Summary:      $SummaryFile"
Write-Host ""
Write-Host "Expected strict-native banner:" -ForegroundColor DarkGray
Write-Host "  OpenSPMNative -> backend openspm.native ... renderer ... no emulator fallback" -ForegroundColor DarkGray

if ($NoLaunch) {
    Write-Host "Launch skipped. Run this when ready:" -ForegroundColor Yellow
    Write-Host ('  "' + $Launcher + '" --game "' + $GameDir + '" --graphics ' + $Graphics)
    exit 0
}

Write-Host "Launching the persistent M14 candidate now..." -ForegroundColor Green
& $Launcher --game $GameDir --graphics $Graphics 2>&1 | Tee-Object -FilePath $LaunchLog
$gameExit = $LASTEXITCODE
Write-Host "OpenSPMNative exit code: $gameExit"
Write-Host "Launch log: $LaunchLog"
exit $gameExit
