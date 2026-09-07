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

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1 -InstallTask
    Register two Windows scheduled tasks: the daily scan and email at 17:45, and
    the monthly snapshot on the first of the month. Asks for the Gmail app
    password once and stores it as a user-scope environment variable (the
    registry under HKCU\Environment, not a file in this folder).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1 -Daily
    What the daily task runs: scan prices, filings and headlines, then send the email.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1 -Monthly
    What the monthly task runs: archive a dated snapshot and rebuild every page.
#>

[CmdletBinding()]
param(
    [switch]$SkipPulls,     # set up only, no network pulls
    [switch]$NoServe,       # do everything except start the server
    [switch]$InstallTask,   # register the daily and monthly scheduled tasks, then exit
    [switch]$UninstallTask, # remove them
    [switch]$Daily,         # run the daily scan and email (what the daily task calls), then exit
    [switch]$Monthly,       # run the monthly snapshot and rebuild (what the monthly task calls), then exit
    [switch]$OnlyIfAlerts,  # with -Daily: send the email only when a written rule fired
    [string]$DailyAt = "17:45"   # local time for the daily task; the close is 16:00 ET
)

$TASK_DAILY = "Desk daily scan and email"
$TASK_MONTHLY = "Desk monthly snapshot"

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

# ---------------------------------------------------------------- scheduled runs
# These branches are what the scheduled tasks call. They run with no prompts and
# no pulls beyond their own, log to data\cache\desk-<name>.log, and exit.

if ($Daily) {
    $log = Join-Path $root "data\cache\desk-daily.log"
    New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
    "==== $(Get-Date -Format s) daily" | Out-File -Append -FilePath $log
    Say "Daily scan"
    & $PY scripts/scan.py --live 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { Warn "the scan did not finish cleanly; the email says what it has" }
    Say "Daily email"
    $emailArgs = @("scripts/daily_email.py", "--send", "--preview")
    if ($OnlyIfAlerts) { $emailArgs += "--only-if-alerts" }
    & $PY @emailArgs 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -eq 2) { Warn "no credential in the environment. Run setup.ps1 -InstallTask once to store it." }
    elseif ($LASTEXITCODE -ne 0) { Warn "the send failed. The log is $log" }
    else { Ok "done" }
    exit $LASTEXITCODE
}

if ($Monthly) {
    $log = Join-Path $root "data\cache\desk-monthly.log"
    New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
    "==== $(Get-Date -Format s) monthly" | Out-File -Append -FilePath $log
    Say "Monthly snapshot (see scripts/an/power.py for why monthly)"
    & $PY scripts/snapshot.py 2>&1 | Tee-Object -FilePath $log -Append
    Say "Grading the journal against prices"
    & $PY scripts/track_calls.py --live 2>&1 | Tee-Object -FilePath $log -Append
    Say "Rebuilding every page"
    & $PY scripts/build_all.py 2>&1 | Tee-Object -FilePath $log -Append
    exit $LASTEXITCODE
}

if ($UninstallTask) {
    foreach ($n in @($TASK_DAILY, $TASK_MONTHLY)) {
        if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $n -Confirm:$false
            Ok "removed '$n'"
        } else { Info "'$n' was not registered" }
    }
    exit 0
}

if ($InstallTask) {
    Say "Email credential"
    Info "The daily email sends through Gmail with an app password (Google account > Security >"
    Info "2-Step Verification > App passwords). It is stored as a user-scope environment variable,"
    Info "which lives in the registry under HKCU\Environment, not in any file in this folder."
    Info "Press Enter on any question to keep what is already stored."
    $cur = [Environment]::GetEnvironmentVariable("DESK_MAIL_USER", "User")
    $user = Read-Host "    Gmail address that sends$(if ($cur) { " [$cur]" })"
    if ([string]::IsNullOrWhiteSpace($user)) { $user = $cur }
    $curTo = [Environment]::GetEnvironmentVariable("DESK_MAIL_TO", "User")
    $to = Read-Host "    Send to (blank = same address)$(if ($curTo) { " [$curTo]" })"
    if ([string]::IsNullOrWhiteSpace($to)) { $to = if ($curTo) { $curTo } else { $user } }
    $hasPw = -not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable("DESK_MAIL_PASSWORD", "User"))
    $pwSecure = Read-Host "    App password$(if ($hasPw) { ' [stored, Enter keeps it]' })" -AsSecureString
    $pw = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($pwSecure))
    if ([string]::IsNullOrWhiteSpace($user)) { Die "an address is needed to send from" }
    if ([string]::IsNullOrWhiteSpace($pw) -and -not $hasPw) { Die "an app password is needed. Nothing was stored." }
    [Environment]::SetEnvironmentVariable("DESK_MAIL_USER", $user, "User")
    [Environment]::SetEnvironmentVariable("DESK_MAIL_TO", $to, "User")
    if (-not [string]::IsNullOrWhiteSpace($pw)) { [Environment]::SetEnvironmentVariable("DESK_MAIL_PASSWORD", $pw, "User") }
    $pw = $null
    Ok "stored for this Windows user. Remove with: [Environment]::SetEnvironmentVariable('DESK_MAIL_PASSWORD', `$null, 'User')"

    $sec = [Environment]::GetEnvironmentVariable("SEC_USER_AGENT", "User")
    $secIn = Read-Host "    Name and email for SEC requests (they ask for it)$(if ($sec) { " [$sec]" })"
    if (-not [string]::IsNullOrWhiteSpace($secIn)) { [Environment]::SetEnvironmentVariable("SEC_USER_AGENT", $secIn.Trim(), "User") }

    Say "Registering the scheduled tasks"
    $ps = (Get-Command powershell).Source
    $script = Join-Path $root "setup.ps1"
    $daily = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Daily" -WorkingDirectory $root
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
    $tDaily = New-ScheduledTaskTrigger -Daily -At $DailyAt
    Register-ScheduledTask -TaskName $TASK_DAILY -Action $daily -Trigger $tDaily -Settings $settings -Force `
        -Description "Scans prices, SEC filings and headlines for the journal's names and emails the digest. setup.ps1 -Daily" | Out-Null
    Ok "'$TASK_DAILY' every day at $DailyAt"
    # New-ScheduledTaskTrigger has no monthly form, so the monthly task goes through
    # schtasks, which does: the first of every month at 18:30.
    $monthlyCmd = "`"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$script`" -Monthly"
    schtasks /Create /F /SC MONTHLY /D 1 /ST 18:30 /TN "$TASK_MONTHLY" /TR $monthlyCmd | Out-Null
    if ($LASTEXITCODE -ne 0) { Warn "schtasks could not register the monthly task; run setup.ps1 -Monthly by hand on the first of the month" }
    else { Ok "'$TASK_MONTHLY' on the first of every month at 18:30" }
    Info "see them in Task Scheduler, or: Get-ScheduledTask -TaskName 'Desk*'"
    Info "try one now:  powershell -ExecutionPolicy Bypass -File setup.ps1 -Daily"
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
