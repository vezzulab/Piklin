# Opens Piklin again once an update has been installed. Started by Piklin as it closes;
# see winupdate.py: the person gave permission, and checking and installing the update
# are waiting (install.cmd, run by an administrator's Piklin) for Piklin to close. This
# only waits for them to finish and says how it went.
param(
    [int]$ParentPid,
    [string]$Root,
    [string]$Stage,
    [string]$Version,
    [string]$Library,
    [string]$ResultFile
)
$ErrorActionPreference = 'Continue'
function Quote([string]$s) { '"' + ($s -replace '"', '\"') + '"' }

$exe = Join-Path $Root 'runtime\bin\Piklin.exe'
$ok = $false
$code = 1

# 1. Wait for Piklin to close (the installation is waiting for that too).
try { Wait-Process -Id $ParentPid -Timeout 120 -ErrorAction Stop } catch { }

# 2. Wait for msiexec to finish and read how it went.
try {
    $info = Get-Content -Raw -Path (Join-Path $Stage 'apply.json') | ConvertFrom-Json
    $deadline = (Get-Date).AddMinutes(30)
    while (-not (Test-Path -LiteralPath $info.result) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    if (Test-Path -LiteralPath $info.result) {
        $code = [int]((Get-Content -Raw -LiteralPath $info.result).Trim())
    }
} catch { }
if ($code -eq 0 -or $code -eq 3010) { $ok = $true }

# 3. Say what happened (the new Piklin shows it), and open Piklin again: in the new
#    version when it worked, the one that was there when it did not.
try {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ResultFile) | Out-Null
    $stamp = [int][double]::Parse((Get-Date -UFormat %s))
    $json = '{"version": "' + $Version + '", "ok": ' + ($ok.ToString().ToLower()) + ', "code": ' + $code + ', "time": ' + $stamp + '}'
    Set-Content -Path $ResultFile -Value $json -Encoding ASCII
} catch { }
if ($Library) {
    Start-Process -FilePath $exe -ArgumentList @('--library', (Quote $Library))
} else {
    Start-Process -FilePath $exe
}
Remove-Item -Recurse -Force -LiteralPath $Stage -ErrorAction SilentlyContinue
