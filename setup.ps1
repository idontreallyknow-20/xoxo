<#
.SYNOPSIS
    Set up and run the desk on Windows, in one command. Safe to run again any time.

.DESCRIPTION
    Finds the project (or clones it), installs the Python libraries, optionally
    runs the live data pulls, and opens the dashboard in a browser.

    Nothing here deletes or overwrites your work. If a step fails it says so and
    carries on to the next one, because most of them are independent.

    API keys are read into the current PowerShell window only. Nothing is written
    to disk and nothing is committed. Close the window and the key is gone.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1 -SkipPulls
    Set up and open the dashboard without fetching any data.
#>

[CmdletBinding()]
param(
    [switch]$SkipPulls,   # set up only, no network pulls
    [switch]$NoServe      # do everything except start the server
)

$REPO_URL = "https://github.com/idontreallyknow-20/xoxo.git"
$PORT = 8765

function Say  ($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "    $m" -ForegroundColor Green }
function Info ($m) { Write-Host "    $m" -ForegroundColor Gray }
function Warn ($m) { Write-Host "    $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host ""; Write-Host "STOPPED: $m" -ForegroundColor Red; Write-Host ""; exit 1 }

function Ask-YesNo ($question, $default = $true) {
    $hint = if ($default) { "[Y/n]" } else { "[y/N]" }
    $answer = Read-Host "    $question $hint"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim().ToLower().StartsWith("y")
}

# ---------------------------------------------------------------- python

# zoneinfo, which scripts/serve.py imports, needs 3.9 or newer.
function Find-Python {
    foreach ($cmd in @("python", "py", "python3")) {
        if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { continue }
        $out = ""
        try { $out = (& $cmd --version 2>&1) -join " " } catch { continue }
        if ($out -match "Python 3\.(\d+)") {
            if ([int]$Matches[1] -ge 9) { return $cmd }
            Warn "$cmd is $out, which is too old. Need Python 3.9 or newer."
        }
    }
    return $null
}

Say "Looking for Python"
$PY = Find-Python
if (-not $PY) {
    Die @"
No usable Python found.

Install it from https://www.python.org/downloads/ and TICK THE BOX that says
"Add python.exe to PATH" on the first screen of the installer. Then close this
window, open a new PowerShell, and run this script again.
"@
}
Ok "using '$PY' ($((& $PY --version 2>&1) -join ' '))"

# ---------------------------------------------------------------- git

Say "Looking for git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "git is not installed. Get it from https://git-scm.com/download/win, then run this script again."
}
Ok "found"

# ---------------------------------------------------------------- the project

function Test-RepoRoot ($path) {
    if (-not $path) { return $false }
    $serve = Join-Path (Join-Path $path "scripts") "serve.py"
    return (Test-Path (Join-Path $path "criteria.md")) -and (Test-Path $serve)
}

Say "Looking for the project folder"
$root = $null

# 1. the folder this script is sitting in
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-RepoRoot $scriptDir) { $root = $scriptDir }

# 2. the current folder
if (-not $root) {
    $here = (Get-Location).Path
    if (Test-RepoRoot $here) { $root = $here }
}

# 3. anywhere under your user folder
if (-not $root) {
    Info "searching your user folder, this can take a minute..."
    $hit = Get-ChildItem -Path $HOME -Filter "criteria.md" -Recurse -File -ErrorAction SilentlyContinue |
           Where-Object { Test-RepoRoot $_.DirectoryName } |
           Select-Object -First 1
    if ($hit) { $root = $hit.DirectoryName }
}

# 4. give up and clone a fresh copy
if (-not $root) {
    $target = Join-Path (Join-Path $HOME "Documents") "xoxo"
    Info "not found on this machine, cloning a fresh copy to $target"
    if (Test-Path $target) {
        Die "$target already exists but does not look like the project. Move or rename it, then run this again."
    }
    git clone $REPO_URL $target
    if ($LASTEXITCODE -ne 0) { Die "git clone failed. Check your internet connection and try again." }
    if (-not (Test-RepoRoot $target)) { Die "the clone finished but $target does not look right. Tell Claude what happened." }
    $root = $target
}

Set-Location -LiteralPath $root -ErrorAction SilentlyContinue
if ((Get-Location).Path -ne $root) { Die "could not open $root. Is it on a drive that is disconnected?" }
Ok "using $root"

# ---------------------------------------------------------------- update

Say "Getting the latest version"
git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -ne 0) {
    Warn "this folder is not a git checkout, skipping the update"
} else {
    $dirty = git status --porcelain
    if ($dirty) {
        Warn "you have local changes here, so nothing was pulled. Your files are untouched."
        Info "if you want the latest version, commit or stash those changes first."
    } else {
        git checkout main *> $null
        git pull origin main
        if ($LASTEXITCODE -ne 0) { Warn "could not pull. Carrying on with the version you already have." }
        else { Ok "up to date" }
    }
}

# ---------------------------------------------------------------- libraries

Say "Installing the Python libraries (pandas, numpy, yfinance)"
& $PY -m pip install --quiet --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Warn "pip had trouble. Trying again with the user flag..."
    & $PY -m pip install --quiet --disable-pip-version-check --user -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Die "could not install the libraries. Copy the error above and show it to Claude." }
}
Ok "done"

# ---------------------------------------------------------------- the pulls

if (-not $SkipPulls) {

    Say "Grading your journal calls against real prices"
    Info "this needs no key. It pulls daily closes for your 16 names plus SPY and QQQ."
    if (Ask-YesNo "Run it now?") {
        & $PY scripts/track_calls.py --live
        if ($LASTEXITCODE -ne 0) { Warn "that did not finish cleanly. The dashboard still works." }
        else { Ok "done, look for 'Every call, graded' at the bottom of /positioning/" }
    }

    Say "SEC filings and fundamentals"
    Info "the SEC asks for a real name and email in the request, no signup, no key."
    $sec = Read-Host "    Your name and email (e.g. Joseph Chen joseph@example.com), or press Enter to skip"
    if (-not [string]::IsNullOrWhiteSpace($sec)) {
        $env:SEC_USER_AGENT = $sec.Trim()

        Info "pulling KLAC's filings..."
        & $PY scripts/fetch_edgar.py --facts --exhibits KLAC
        if ($LASTEXITCODE -ne 0) { Warn "the EDGAR pull did not finish cleanly." }

        if (Ask-YesNo "Also download the SEC bulk data sets? They are 50-100 MB per quarter." $false) {
            & $PY scripts/fetch_dera.py --since 2023q1
            if ($LASTEXITCODE -ne 0) { Warn "the download did not finish cleanly." }
            else {
                Info "checking it against Apple's 10-K, this should print 383,285,000,000 for FY2023:"
                & $PY scripts/fetch_dera.py --show 320193 --metric revenue
            }
        }
    }

    Say "Survivorship check"
    Info "free key from https://www.alphavantage.co/support/#api-key, takes 30 seconds."
    Info "your key is used in this window only and is never saved anywhere."
    $av = Read-Host "    Paste your Alpha Vantage key, or press Enter to skip"
    if (-not [string]::IsNullOrWhiteSpace($av)) {
        $env:ALPHAVANTAGE_KEY = $av.Trim()
        & $PY scripts/listing_status.py --fetch
        if ($LASTEXITCODE -ne 0) { Warn "that did not finish cleanly. It costs 2 of your 25 daily requests." }
    }
}

# ---------------------------------------------------------------- serve

if ($NoServe) {
    Say "Done"
    Info "start the dashboard later with:  $PY scripts/serve.py"
    exit 0
}

Say "Starting the dashboard"
Info "opening http://localhost:$PORT in your browser"
Info "the front page refreshes its own data on startup, which takes a minute and prints messages."
Info "the new Analyse and Positioning tabs work immediately and do not wait for it."
Write-Host ""
Write-Host "    Press Ctrl+C in this window when you want to stop the server." -ForegroundColor Yellow
Write-Host ""

try {
    Start-Job -ScriptBlock {
        Start-Sleep -Seconds 3
        Start-Process "http://localhost:$using:PORT"
    } | Out-Null
} catch {
    Warn "could not open your browser automatically. Go to http://localhost:$PORT yourself."
}

& $PY scripts/serve.py
