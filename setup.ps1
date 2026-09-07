<#
.SYNOPSIS
    Set up and run the desk on Windows, in one command. Safe to run again any time.

.DESCRIPTION
    Finds the project (or clones it), installs the Python libraries, optionally
    runs the live data pulls, rebuilds every page, and opens the dashboard.

    Nothing here deletes or overwrites your work. If a step fails it says so and
    carries on to the next one, because most of them are independent.

    API keys are read into the current PowerShell window only. Nothing is written
    to disk and nothing is committed. Close the window and the key is gone.

.EXAMPLE
    cd $HOME\Documents\xoxo
    powershell -ExecutionPolicy Bypass -File setup.ps1

    The normal run. Sets up, asks before each pull, rebuilds, opens the dashboard.

    Note the cd. PowerShell resolves -File before this script runs, so a bare
    "setup.ps1" only works from the folder the file is in. The project-finding
    code below runs afterwards and cannot help you get here. From somewhere else,
    give the full path: -File C:\Users\you\Documents\xoxo\setup.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1 -SkipPulls
    Set up and open the dashboard without fetching any data.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1 -InstallTask
    Register the monthly job with Windows so you never have to remember it.
    This is the single most valuable thing in this file. See "Why monthly" below.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1 -Monthly
    The monthly job itself, run by hand. No questions, no browser, no keys needed.
    Reruns the screening pipeline and archives a dated snapshot of what it saw.

.NOTES
    Why monthly, and why it is not the same as trading monthly.

    A snapshot is a recording of what the screen said on a day. It is not a
    prompt to do anything. The scorecard cannot be tested until several of them
    exist, and the cadence decides how long that takes: quarterly archiving
    means three years before any verdict is possible, monthly means one year.
    That is measured, in scripts/an/power.py, not guessed.

    criteria.md still says re-underwrite quarterly and that is still right.
    Archive monthly, decide quarterly. They are different activities.
#>

[CmdletBinding()]
param(
    [switch]$SkipPulls,           # set up only, no network pulls
    [switch]$NoServe,             # do everything except start the server
    [switch]$Monthly,             # unattended: rerun the pipeline, rebuild, archive a snapshot
    [switch]$InstallTask,         # register the monthly job with Windows Task Scheduler
    [switch]$RemoveTask,          # unregister it
    [switch]$Verify,              # install pytest and run the test suite
    [string]$DeraSince = "2016q1" # first SEC quarter to download. 2016q1 is ten years.
)

$REPO_URL = "https://github.com/idontreallyknow-20/xoxo.git"
$PORT = 8765
$TASK_NAME = "Stock desk monthly snapshot"

# The sixteen names with a hand-written research note. The guidance diff only
# makes sense for names somebody has actually read.
$RESEARCHED = @("ACN","ADBE","ADSK","AMAT","BKNG","CPRT","GOOGL","IDXX",
                "ISRG","KLAC","LRCX","META","MSFT","NOW","NVR","REGN")

function Say  ($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "    $m" -ForegroundColor Green }
function Info ($m) { Write-Host "    $m" -ForegroundColor Gray }
function Warn ($m) { Write-Host "    $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host ""; Write-Host "STOPPED: $m" -ForegroundColor Red; Write-Host ""; exit 1 }

function Ask-YesNo ($question, $default = $true) {
    if ($Monthly) { return $default }   # the unattended job never asks
    $hint = if ($default) { "[Y/n]" } else { "[y/N]" }
    $answer = Read-Host "    $question $hint"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim().ToLower().StartsWith("y")
}

# Run a python script, report how it went, and never stop the whole run over it.
function Step ($py, $label, [string[]]$argv) {
    if (-not [string]::IsNullOrWhiteSpace($label)) { Info "$label" }
    & $py @argv
    if ($LASTEXITCODE -ne 0) {
        Warn "'$($argv -join ' ')' exited $LASTEXITCODE. Carrying on."
        return $false
    }
    return $true
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

# ---------------------------------------------------------------- the monthly task

if ($RemoveTask) {
    Say "Removing the monthly job"
    schtasks /delete /tn "$TASK_NAME" /f
    if ($LASTEXITCODE -ne 0) { Warn "could not remove it. It may not have been registered." }
    else { Ok "gone. Nothing will run on its own any more." }
    exit 0
}

if ($InstallTask) {
    Say "Registering the monthly job with Windows"
    $me = Join-Path $root "setup.ps1"
    if (-not (Test-Path $me)) { Die "cannot find setup.ps1 in $root, so there is nothing to schedule." }

    $cmd = "powershell -ExecutionPolicy Bypass -NoProfile -File `"$me`" -Monthly"
    schtasks /create /tn "$TASK_NAME" /tr "$cmd" /sc MONTHLY /d 1 /st 09:00 /f
    if ($LASTEXITCODE -ne 0) {
        Warn "could not register it automatically."
        Info "Open Task Scheduler yourself, create a monthly task, and point it at:"
        Info "  $cmd"
        exit 1
    }
    Ok "done. It runs on the 1st of every month at 9am."
    Info "your laptop has to be awake at the time. If it is usually shut, run"
    Info "  powershell -ExecutionPolicy Bypass -File setup.ps1 -Monthly"
    Info "by hand whenever you remember. It is safe to run twice in a month."
    Write-Host ""
    Info "remove it later with:  powershell -ExecutionPolicy Bypass -File setup.ps1 -RemoveTask"
    exit 0
}

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

# ---------------------------------------------------------------- the monthly job

if ($Monthly) {
    Say "Monthly job: rerun the screen and archive what it saw"
    Info "this is a recording, not a signal to trade. Re-underwrite quarterly, per criteria.md."
    Info "expect 15 to 25 minutes, most of it in the fundamentals pull."
    Write-Host ""

    Step $PY "step 1 of 6: the universe"          @("scripts/universe.py")       | Out-Null
    Step $PY "step 2 of 6: four years of statements (cached 30 days, so this is the slow one)" `
                                                  @("scripts/fundamentals.py")   | Out-Null
    Step $PY "step 3 of 6: quality score, top 150" @("scripts/quality_screen.py") | Out-Null
    Step $PY "step 4 of 6: price screen and buckets" @("scripts/price_screen.py") | Out-Null
    Step $PY "step 5 of 6: rebuild every page"     @("scripts/build_dashboard.py") | Out-Null
    Step $PY ""                                    @("scripts/build_all.py")      | Out-Null

    Say "step 6 of 6: archiving the snapshot"
    & $PY scripts/snapshot.py
    if ($LASTEXITCODE -ne 0) { Warn "the snapshot did not finish cleanly." }

    Say "Where the archive stands"
    & $PY scripts/snapshot.py --report

    Say "Monthly job done"
    Info "nothing here decided anything. Look at /positioning/ when you next re-underwrite."
    exit 0
}

# ---------------------------------------------------------------- the pulls

if (-not $SkipPulls) {

    Say "Grading your journal calls against real prices"
    Info "no key needed. Pulls daily closes for your 16 names plus SPY and QQQ."
    if (Ask-YesNo "Run it now?") {
        Step $PY "" @("scripts/track_calls.py", "--live") | Out-Null
        Ok "look for 'Every call, graded' near the bottom of /positioning/"
        Info "the read path under it stays INSUFFICIENT until there are 20 graded calls"
        Info "on 4 separate dates. Right now every call is dated 2026-09-04, so it will"
        Info "refuse to report a correlation, which is correct."
    }

    Say "SEC filings"
    Info "the SEC asks for a real name and email in the request. No signup, no key, no cost."
    $sec = if ($Monthly) { "" } else { Read-Host "    Your name and email (e.g. Joseph Chen joseph@example.com), or press Enter to skip" }
    if (-not [string]::IsNullOrWhiteSpace($sec)) {
        $env:SEC_USER_AGENT = $sec.Trim()

        # This caches the ticker-to-CIK map, which everything else needs to turn
        # "AAPL" into "0000320193". Cheap, and the DERA step is useless without it.
        Step $PY "caching the ticker map, and pulling KLAC as a sanity check..." `
                 @("scripts/fetch_edgar.py", "--facts", "--exhibits", "KLAC") | Out-Null

        Say "SEC as-reported fundamentals (the big one)"
        Info "this replaces the four restated years of Yahoo data the whole score sits on"
        Info "with ten years of filings, dated by when they were filed. It is the reason"
        Info "criteria.md's 'a paid source fixes this for 20 to 80 dollars a month' is now moot."
        Warn "cost: about 40 files, 50 to 100 MB each. Call it 2 to 4 GB and up to an hour."
        Info "it is resumable. Stop it any time and run this script again to carry on."
        if (Ask-YesNo "Download them?" $false) {
            Step $PY "downloading from $DeraSince..." @("scripts/fetch_dera.py", "--since", $DeraSince) | Out-Null

            Info ""
            Info "checking it against Apple's 10-K. FY2023 net sales should read 383,285,000,000:"
            & $PY scripts/fetch_dera.py --show 320193 --metric revenue

            Info ""
            Info "and here is what the score will actually read, name by name,"
            Info "with every place the SEC filings disagree with Yahoo:"
            & $PY scripts/fetch_dera.py --basis-report
        }

        Say "Guidance changes from the 8-K press releases"
        Info "the last two earnings releases per name, diffed for what management guided."
        Info "This is the free stand-in for a transcript. About 5 requests per name."
        if (Ask-YesNo "Run it for your 16 researched names?") {
            Step $PY "" (@("scripts/guidance_diff.py") + $RESEARCHED) | Out-Null
            Ok "look under 'What changed since the last report' on those pages"
        }
    }

    Say "Survivorship check"
    Info "free key from https://www.alphavantage.co/support/#api-key, takes 30 seconds."
    Info "it costs 2 of your 25 daily requests and turns 'flattering by an unknown"
    Info "amount' in every backtest limitation into an actual measured number."
    Info "your key is used in this window only and is never saved anywhere."
    $av = if ($Monthly) { "" } else { Read-Host "    Paste your Alpha Vantage key, or press Enter to skip" }
    if (-not [string]::IsNullOrWhiteSpace($av)) {
        $env:ALPHAVANTAGE_KEY = $av.Trim()
        Step $PY "" @("scripts/listing_status.py", "--fetch") | Out-Null
    }

    # Whatever came back above, the pages only show it after a rebuild.
    Say "Rebuilding the pages from whatever was fetched"
    Step $PY "" @("scripts/build_all.py") | Out-Null
    Ok "done"
}

# ---------------------------------------------------------------- verify

if ($Verify) {
    Say "Running the test suite"
    Info "pytest is not in requirements.txt, so installing it now."
    & $PY -m pip install --quiet --disable-pip-version-check pytest
    & $PY -m pytest -q
    if ($LASTEXITCODE -ne 0) { Warn "something failed. Copy the output above and show it to Claude." }
    else { Ok "all green" }
}

# ---------------------------------------------------------------- the nag

Say "One thing worth doing before you close this"
$taskExists = $false
schtasks /query /tn "$TASK_NAME" *> $null
if ($LASTEXITCODE -eq 0) { $taskExists = $true }

if ($taskExists) {
    Ok "the monthly snapshot job is registered. Nothing to do."
} else {
    Info "Nothing in this project can tell you whether the score works until several"
    Info "dated snapshots exist. There is one. Archiving monthly instead of quarterly"
    Info "is the difference between a first verdict in one year and in three."
    Write-Host ""
    Info "  powershell -ExecutionPolicy Bypass -File setup.ps1 -InstallTask"
    Write-Host ""
    Info "sets that up once and you never think about it again."
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
Info "the Analyse and Positioning tabs work immediately and do not wait for it."
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
