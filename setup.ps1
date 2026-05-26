# GRS v15.0 Phase 1 - GitHub one-shot setup (PowerShell, Windows native)
#
# Runs:
#   1) Check gh CLI + git installed and authenticated
#   2) Create GitHub repo (gh repo create)
#   3) Push 8 files from this folder
#   4) Register 3 secrets (NOTION_TOKEN, NOTION_DATABASE_ID, OPENFDA_API_KEY)
#   5) Print verification URLs
#
# Token is read via SecureString prompt (hidden input + memory protection).
# Never echoed to terminal, git history, or log files.
#
# Usage:
#   cd "C:\Users\user\Desktop\Global Regulatory Sweep\v15.0-implementation"
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
#
# Pre-set env vars (optional):
#   $env:NOTION_TOKEN='ntn_...'; $env:NOTION_DATABASE_ID='7784...'; .\setup.ps1

[CmdletBinding()]
param(
    # Codex Pass 3 should-fix: validate $RepoName at parameter binding to prevent
    # quote/space injection into the gh secret set ProcessStartInfo Arguments string.
    # GitHub repository name rules allow [A-Za-z0-9._-] only.
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$RepoName = "grs-api-intake",
    [ValidateSet("public","private")][string]$Visibility = "public",
    [string]$NotionDatabaseId = "7784c71fb7b343749b2bee5d04db7926"
)

# NOTE: We intentionally do NOT set $ErrorActionPreference = "Stop" globally.
# Reason: native commands (gh, git) write to stderr in normal operation
# (e.g. "Could not resolve to a Repository" when checking if a repo exists),
# and EAP=Stop turns those into script-terminating NativeCommandError exceptions.
# Instead we use explicit $LASTEXITCODE checks and try/catch only where needed.

$ErrorActionPreference = "Continue"

# ---- Constants ----
$RequiredFiles = @(
    "collect_intake.py",
    "requirements.txt",
    ".gitignore",
    ".env.example",
    "README.md",
    "notion_intake_db_schema.md",
    "GRS_Prompt_v15.0.md",
    ".github/workflows/grs-intake.yml"
)

function Write-Title($t) { Write-Host ""; Write-Host "-- $t --" -ForegroundColor White }
function Write-Ok($t)    { Write-Host "[OK]   $t" -ForegroundColor Green }
function Write-Warn($t)  { Write-Host "[WARN] $t" -ForegroundColor Yellow }
function Write-Err($t)   { Write-Host "[ERR]  $t" -ForegroundColor Red }
function Write-Info($t)  { Write-Host "[INFO] $t" -ForegroundColor Cyan }

function Fail-And-Exit($msg, [int]$code = 1) {
    Write-Err $msg
    exit $code
}

function Read-SecretInput($prompt) {
    $ss = Read-Host -Prompt $prompt -AsSecureString
    if (-not $ss -or $ss.Length -eq 0) { return "" }
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss)
    try { return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

# Run a native command, capture stdout, return exit code in $script:LastNativeExit.
# Native commands writing to stderr will not throw, regardless of EAP.
function Invoke-Native {
    param(
        [Parameter(Mandatory=$true)][scriptblock]$Command,
        [switch]$IgnoreStderr
    )
    $script:LastNativeExit = 0
    try {
        if ($IgnoreStderr) {
            $out = & $Command 2>$null
        } else {
            $out = & $Command 2>&1 | ForEach-Object { "$_" }
        }
        $script:LastNativeExit = $LASTEXITCODE
        return $out
    } catch {
        $script:LastNativeExit = if ($LASTEXITCODE) { $LASTEXITCODE } else { 1 }
        return $null
    }
}

# ---- 1. Preflight ----
Write-Title "1. Preflight checks"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail-And-Exit "git not found in PATH. Install from https://git-scm.com"
}
Write-Ok "git found"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Fail-And-Exit "gh (GitHub CLI) not found. Install from https://cli.github.com then run 'gh auth login'"
}
Write-Ok "gh CLI found"

Invoke-Native { gh auth status } -IgnoreStderr | Out-Null
if ($script:LastNativeExit -ne 0) {
    Fail-And-Exit "gh is not logged in. Run 'gh auth login' first."
}

$ghUserRaw = Invoke-Native { gh api user --jq .login } -IgnoreStderr
if ($script:LastNativeExit -ne 0 -or -not $ghUserRaw) {
    Fail-And-Exit "Failed to query GitHub username via 'gh api user'."
}
$ghUser = ($ghUserRaw | Out-String).Trim()
Write-Ok "gh authenticated as: $ghUser"

$missing = @()
foreach ($f in $RequiredFiles) {
    if (-not (Test-Path -LiteralPath $f)) { $missing += $f }
}
if ($missing.Count -gt 0) {
    Write-Err ("Missing files in current folder: " + ($missing -join ", "))
    Fail-And-Exit "Run this script from the v15.0-implementation folder."
}
Write-Ok "All 8 required files present"

# ---- 2. Collect inputs ----
Write-Title "2. Inputs"

$tmp = Read-Host "Repo name [$RepoName]"
if ($tmp) { $RepoName = $tmp }
# Codex Pass 3 should-fix: parameter ValidatePattern doesn't catch values
# assigned interactively. Re-validate.
if ($RepoName -notmatch '^[A-Za-z0-9._-]+$') {
    Fail-And-Exit "Invalid repo name '$RepoName'. Allowed characters: A-Z a-z 0-9 . _ -"
}

$tmp = Read-Host "Visibility public/private [$Visibility]"
if ($tmp) { $Visibility = $tmp }
if ($Visibility -notin @("public","private")) {
    Fail-And-Exit "Invalid visibility '$Visibility'. Must be 'public' or 'private'."
}

$tmp = Read-Host "Notion Database ID [$NotionDatabaseId]"
if ($tmp) { $NotionDatabaseId = $tmp }

$NotionToken = if ($env:NOTION_TOKEN) { $env:NOTION_TOKEN } else { "" }
if (-not $NotionToken) {
    Write-Host "Paste your Notion Integration token. (Input is hidden.)"
    $NotionToken = Read-SecretInput "NOTION_TOKEN"
}
if (-not $NotionToken) {
    Fail-And-Exit "NOTION_TOKEN is empty. Aborting."
}

$OpenfdaKey = if ($env:OPENFDA_API_KEY) { $env:OPENFDA_API_KEY } else { "" }
if (-not $OpenfdaKey) {
    Write-Host "OpenFDA API key (optional - press Enter to skip)"
    $OpenfdaKey = Read-SecretInput "OPENFDA_API_KEY"
}

Write-Host ""
Write-Info "Summary:"
Write-Host "  - Repo            : $ghUser/$RepoName ($Visibility)"
Write-Host "  - Notion DB ID    : $NotionDatabaseId"
$tokPreview = $NotionToken.Substring(0, [Math]::Min(4, $NotionToken.Length))
Write-Host ("  - NOTION_TOKEN    : ({0} chars, starts with {1}***)" -f $NotionToken.Length, $tokPreview)
if ($OpenfdaKey) {
    Write-Host ("  - OpenFDA key     : ({0} chars) - will register" -f $OpenfdaKey.Length)
} else {
    Write-Host "  - OpenFDA key     : not provided - secret skipped (collector runs in no-key mode)"
}
Write-Host ""
$confirm = Read-Host "Proceed? [y/N]"
if ($confirm -notmatch "^[Yy]$") {
    Write-Warn "Cancelled."
    exit 0
}

# ---- 3. Create repo ----
Write-Title "3. Create repository"

$repoUrl = "https://github.com/$ghUser/$RepoName"

Invoke-Native { gh repo view "$ghUser/$RepoName" } -IgnoreStderr | Out-Null
$exists = ($script:LastNativeExit -eq 0)

if ($exists) {
    Write-Warn "Repo $ghUser/$RepoName already exists."
    $resume = Read-Host "Push to the existing repo? [y/N]"
    if ($resume -notmatch "^[Yy]$") { Fail-And-Exit "Aborted by user." }
    Write-Ok "Using existing repo: $repoUrl"
} else {
    $visFlag = "--$Visibility"
    $createOut = Invoke-Native {
        gh repo create $RepoName $visFlag `
            --description "GRS API Intake - Federal Register + OpenFDA weekly collector for Claude Routine v15.0" `
            --disable-wiki
    }
    if ($script:LastNativeExit -ne 0) {
        Write-Err ($createOut -join "`n")
        Fail-And-Exit "gh repo create failed."
    }
    Write-Ok "Repo created: $repoUrl"
}

# ---- 4. Git init + push ----
Write-Title "4. git push"

if (-not (Test-Path -LiteralPath ".git")) {
    Invoke-Native { git init -b main } -IgnoreStderr | Out-Null
    if ($script:LastNativeExit -ne 0) {
        # Older git may not support -b on init; fall back
        Invoke-Native { git init } -IgnoreStderr | Out-Null
        Invoke-Native { git checkout -b main } -IgnoreStderr | Out-Null
    }
    Write-Ok "git init (main)"
}

$originRaw = Invoke-Native { git remote get-url origin } -IgnoreStderr
$currentOrigin = ""
if ($script:LastNativeExit -eq 0 -and $originRaw) {
    $currentOrigin = ($originRaw | Out-String).Trim()
}

if ($currentOrigin) {
    if ($currentOrigin -ne "$repoUrl.git" -and $currentOrigin -ne $repoUrl) {
        Write-Warn "origin points elsewhere: $currentOrigin"
        Invoke-Native { git remote set-url origin "$repoUrl.git" } -IgnoreStderr | Out-Null
        Write-Ok "origin reset to: $repoUrl.git"
    }
} else {
    Invoke-Native { git remote add origin "$repoUrl.git" } -IgnoreStderr | Out-Null
    Write-Ok "origin added"
}

# Git identity (set if missing)
$cfgEmail = Invoke-Native { git config user.email } -IgnoreStderr
$cfgName  = Invoke-Native { git config user.name } -IgnoreStderr
if (-not $cfgEmail) { Invoke-Native { git config user.email "$ghUser@users.noreply.github.com" } -IgnoreStderr | Out-Null }
if (-not $cfgName)  { Invoke-Native { git config user.name "$ghUser" } -IgnoreStderr | Out-Null }

Invoke-Native { git add . } -IgnoreStderr | Out-Null

# Codex nice-to-have: distinguish git diff --cached exit codes.
#   0  = no staged changes (nothing to commit)
#   1  = staged changes present (proceed to commit)
#   >1 = unexpected git error (corrupted repo, bad refs, etc.) - fail loudly
Invoke-Native { git diff --cached --quiet } -IgnoreStderr | Out-Null
$diffExit = $script:LastNativeExit

if ($diffExit -eq 0) {
    Write-Warn "Nothing to commit (already pushed?)."
} elseif ($diffExit -eq 1) {
    Invoke-Native { git commit -m "Initial v15.0 Phase 1 - intake collector + workflow + Routine prompt" } -IgnoreStderr | Out-Null
    if ($script:LastNativeExit -ne 0) {
        Fail-And-Exit "git commit failed (exit $($script:LastNativeExit))."
    }
    Write-Ok "Commit created"
} else {
    Fail-And-Exit "git diff --cached failed with unexpected exit code $diffExit. Repository may be corrupted."
}

Invoke-Native { git branch -M main } -IgnoreStderr | Out-Null

# Codex should-fix: push failure must propagate to setup exit code; previously
# we printed a warning and then claimed success at the end.
$pushOut = Invoke-Native { git push -u origin main }
$script:PushSuccess = ($script:LastNativeExit -eq 0)
if ($script:PushSuccess) {
    Write-Ok "push complete"
} else {
    Write-Warn "push failed:"
    Write-Host ($pushOut -join "`n")
    Write-Warn "Continuing with secret registration (idempotent), but setup will exit non-zero."
}

# ---- 5. Register secrets ----
Write-Title "5. Register GitHub Secrets"

function Set-RepoSecret($name, $value) {
    # Codex should-fix #3: never pass the secret via `--body $value`, because that
    # puts it in the gh process argv. GitHub CLI reads the secret from stdin when
    # --body is omitted. Use ProcessStartInfo so we can write exact bytes without
    # PowerShell pipeline newline conversion. Keep this Windows PowerShell 5.1
    # compatible: avoid ProcessStartInfo.ArgumentList and *Encoding properties
    # that are not available on older .NET Framework hosts.
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = "gh"
    $psi.Arguments = "secret set `"$name`" --repo `"$ghUser/$RepoName`""
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.StandardInput.Write($value)
    $proc.StandardInput.Close()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()

    if ($proc.ExitCode -ne 0) {
        if ($stdout) { Write-Host $stdout }
        if ($stderr) { Write-Err $stderr }
        throw "gh secret set $name failed (exit $($proc.ExitCode))"
    }
}

try {
    Set-RepoSecret "NOTION_TOKEN" $NotionToken
    Write-Ok "NOTION_TOKEN registered"

    Set-RepoSecret "NOTION_DATABASE_ID" $NotionDatabaseId
    Write-Ok "NOTION_DATABASE_ID registered"

    if ($OpenfdaKey) {
        Set-RepoSecret "OPENFDA_API_KEY" $OpenfdaKey
        Write-Ok "OPENFDA_API_KEY registered"
    } else {
        Write-Info "OPENFDA_API_KEY skipped (collector runs in no-key mode)"
    }
} catch {
    Write-Err $_.Exception.Message
    Fail-And-Exit "Failed to register one or more secrets. Re-run after fixing."
}

Write-Host ""
Write-Info "Current secrets:"
Invoke-Native { gh secret list --repo "$ghUser/$RepoName" } | Out-Host

# Wipe tokens from memory
$NotionToken = $null
$OpenfdaKey = $null
[System.GC]::Collect()

# ---- 6. Done ----
Write-Title "6. Setup complete"

# Codex should-fix: don't claim success if git push failed earlier.
if (-not $script:PushSuccess) {
    Write-Err "Setup completed with errors: git push failed."
    Write-Host "Resolve the push issue and re-run 'git push -u origin main' manually."
    Write-Host "Secrets were still registered (idempotent), so on retry you only need to push."
    Write-Host ""
    Write-Info "Repo URL: $repoUrl"
    Write-Info "Actions:  $repoUrl/actions"
    Write-Info "Secrets:  $repoUrl/settings/secrets/actions"
    exit 1
}

Write-Ok "All steps succeeded."
Write-Host ""
Write-Host "Next steps (manual):"
Write-Host "  1) In Notion, connect the Integration to the 'Global Regulatory Sweep' parent page"
Write-Host "     (Notion -> parent page -> ... -> Connections -> add the integration)"
Write-Host ""
Write-Host "  2) Trigger a manual dry-run to verify:"
Write-Host "     $repoUrl/actions/workflows/grs-intake.yml"
Write-Host "     -> Run workflow -> dry_run: true"
Write-Host ""
Write-Host "  3) If dry-run is OK, run again with dry_run: false to write to Notion"
Write-Host ""
Write-Host "  4) Paste the contents of GRS_Prompt_v15.0.md into your Claude Code Routine"
Write-Host ""
Write-Host "  5) Cron schedule: every Sunday 22:07 UTC (Monday 07:07 KST)"
Write-Host ""
Write-Info "Repo URL: $repoUrl"
Write-Info "Actions:  $repoUrl/actions"
Write-Info "Secrets:  $repoUrl/settings/secrets/actions"
