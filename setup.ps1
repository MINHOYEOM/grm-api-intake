# GRM current release - GitHub one-shot setup (PowerShell, Windows native)
#
# Runs:
#   1) Check gh CLI + git installed and authenticated
#   2) Create or reuse the GitHub repo
#   3) Push the current GRM implementation from this folder
#   4) Register required and optional GitHub Actions secrets
#   5) Print verification URLs and manual next steps
#
# Required secrets for the default scheduled workflow:
#   NOTION_TOKEN, NOTION_DATABASE_ID, DATA_GO_KR_SERVICE_KEY
#
# Optional secrets:
#   OPENFDA_API_KEY, BRAVE_API_KEY, DATA_GO_KR_KEY
#
# Token values are read via SecureString prompt when not provided as env vars.
# They are never echoed to terminal, git history, or log files.
#
# Usage:
#   cd "C:\Users\user\Desktop\Global Regulatory Sweep\v15.0-implementation"
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
#
# Pre-set env vars (optional):
#   $env:NOTION_TOKEN='ntn_...'
#   $env:NOTION_DATABASE_ID='7784...'
#   $env:DATA_GO_KR_SERVICE_KEY='...'
#   .\setup.ps1

[CmdletBinding()]
param(
    [string]$RepoName = "grm-api-intake",
    [ValidateSet("public","private")][string]$Visibility = "public",
    [string]$NotionDatabaseId = "7784c71fb7b343749b2bee5d04db7926"
)

# NOTE: We intentionally do NOT set $ErrorActionPreference = "Stop" globally.
# Native commands (gh, git) may write to stderr in normal operation. We use
# explicit $LASTEXITCODE checks instead.
$ErrorActionPreference = "Continue"

# ---- Constants ----
$RequiredFiles = @(
    "collect_intake.py",
    "collect_mfds.py",
    "collect_mfds_recall.py",
    "collect_mfds_admin_action.py",
    "collect_mfds_gmp_inspection.py",
    "collect_search.py",
    "collect_ich.py",
    "collect_who.py",
    "collect_hc.py",
    "grm_common.py",
    "requirements.txt",
    ".gitignore",
    ".env.example",
    "GRM_SYSTEM.md",
    "docs/notion_intake_db_schema.md",
    "docs/prompts/GRM_Prompt_v15.6.md",
    ".github/workflows/grm-intake.yml"
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

function Get-SecretValue($name, [switch]$Required, [string]$HelpText = "") {
    $value = [Environment]::GetEnvironmentVariable($name)
    if (-not $value) {
        if ($HelpText) { Write-Host $HelpText }
        $suffix = if ($Required) { "" } else { " (optional - press Enter to skip)" }
        $value = Read-SecretInput "$name$suffix"
    }
    if ($Required -and -not $value) {
        Fail-And-Exit "$name is empty. Aborting."
    }
    return $value
}

function Write-SecretSummary($label, $value, [switch]$Required) {
    if ($value) {
        Write-Host ("  - {0,-23}: ({1} chars)" -f $label, $value.Length)
    } elseif ($Required) {
        Write-Host ("  - {0,-23}: missing" -f $label)
    } else {
        Write-Host ("  - {0,-23}: not provided - secret skipped" -f $label)
    }
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
Write-Ok ("Core GRM files present ({0} checked)" -f $RequiredFiles.Count)

# ---- 2. Collect inputs ----
Write-Title "2. Inputs"

$tmp = Read-Host "Repo name [$RepoName]"
if ($tmp) { $RepoName = $tmp }

$tmp = Read-Host "Visibility public/private [$Visibility]"
if ($tmp) { $Visibility = $tmp }

$envDbId = [Environment]::GetEnvironmentVariable("NOTION_DATABASE_ID")
if ($envDbId) { $NotionDatabaseId = $envDbId }
$tmp = Read-Host "Notion Database ID [$NotionDatabaseId]"
if ($tmp) { $NotionDatabaseId = $tmp }
if (-not $NotionDatabaseId) {
    Fail-And-Exit "NOTION_DATABASE_ID is empty. Aborting."
}

$NotionToken = Get-SecretValue "NOTION_TOKEN" -Required -HelpText "Paste your Notion Integration token. Input is hidden."
$DataGoServiceKey = Get-SecretValue "DATA_GO_KR_SERVICE_KEY" -Required -HelpText "Paste your data.go.kr service key for MFDS Recall/Admin API. Input is hidden."
$OpenfdaKey = Get-SecretValue "OPENFDA_API_KEY" -HelpText "OpenFDA API key is optional. Leave empty for no-key mode."
$BraveKey = Get-SecretValue "BRAVE_API_KEY" -HelpText "Brave Search API key is optional and used only when ENABLE_SEARCH=true."
$DataGoKey = Get-SecretValue "DATA_GO_KR_KEY" -HelpText "DATA_GO_KR_KEY is optional and used only when ENABLE_MOLEG_API=true."

Write-Host ""
Write-Info "Summary:"
Write-Host "  - Repo                   : $ghUser/$RepoName ($Visibility)"
Write-Host "  - Notion DB ID           : $NotionDatabaseId"
Write-SecretSummary "NOTION_TOKEN" $NotionToken -Required
Write-SecretSummary "DATA_GO_KR_SERVICE_KEY" $DataGoServiceKey -Required
Write-SecretSummary "OPENFDA_API_KEY" $OpenfdaKey
Write-SecretSummary "BRAVE_API_KEY" $BraveKey
Write-SecretSummary "DATA_GO_KR_KEY" $DataGoKey
Write-Host ""
Write-Info "Default scheduled runs enable MFDS Recall/Admin/GMP, so DATA_GO_KR_SERVICE_KEY is required."
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
            --description "GRM API Intake - daily regulatory collector for Notion and Claude Routine" `
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

$cfgEmail = Invoke-Native { git config user.email } -IgnoreStderr
$cfgName  = Invoke-Native { git config user.name } -IgnoreStderr
if (-not $cfgEmail) { Invoke-Native { git config user.email "$ghUser@users.noreply.github.com" } -IgnoreStderr | Out-Null }
if (-not $cfgName)  { Invoke-Native { git config user.name "$ghUser" } -IgnoreStderr | Out-Null }

Invoke-Native { git add . } -IgnoreStderr | Out-Null
Invoke-Native { git diff --cached --quiet } -IgnoreStderr | Out-Null
$hasStaged = ($script:LastNativeExit -ne 0)

if (-not $hasStaged) {
    Write-Warn "Nothing to commit (already pushed?)."
} else {
    Invoke-Native { git commit -m "Initial GRM daily intake setup" } -IgnoreStderr | Out-Null
    if ($script:LastNativeExit -ne 0) {
        Fail-And-Exit "git commit failed."
    }
    Write-Ok "Commit created"
}

Invoke-Native { git branch -M main } -IgnoreStderr | Out-Null

$pushOut = Invoke-Native { git push -u origin main }
if ($script:LastNativeExit -eq 0) {
    Write-Ok "push complete"
} else {
    Write-Warn "push failed:"
    Write-Host ($pushOut -join "`n")
    Write-Warn "Try 'git push -u origin main' manually after fixing the cause."
}

# ---- 5. Register secrets ----
Write-Title "5. Register GitHub Secrets"

function Set-RepoSecret($name, $value) {
    $tmp = New-TemporaryFile
    try {
        $acl = Get-Acl -LiteralPath $tmp.FullName
        $acl.SetAccessRuleProtection($true, $false)
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
            'FullControl', 'Allow')
        $acl.SetAccessRule($rule)
        Set-Acl -LiteralPath $tmp.FullName -AclObject $acl

        [System.IO.File]::WriteAllText($tmp.FullName, $value, [System.Text.UTF8Encoding]::new($false))
        Invoke-Native { gh secret set $name --repo "$ghUser/$RepoName" --body-file $tmp.FullName } | Out-Null
        if ($script:LastNativeExit -ne 0) {
            throw "gh secret set $name failed (exit $($script:LastNativeExit))"
        }
    } finally {
        if (Test-Path -LiteralPath $tmp.FullName) {
            [System.IO.File]::WriteAllText($tmp.FullName, ("`0" * $value.Length),
                [System.Text.UTF8Encoding]::new($false))
            Remove-Item -Force -LiteralPath $tmp.FullName
        }
    }
}

try {
    Set-RepoSecret "NOTION_TOKEN" $NotionToken
    Write-Ok "NOTION_TOKEN registered"

    Set-RepoSecret "NOTION_DATABASE_ID" $NotionDatabaseId
    Write-Ok "NOTION_DATABASE_ID registered"

    Set-RepoSecret "DATA_GO_KR_SERVICE_KEY" $DataGoServiceKey
    Write-Ok "DATA_GO_KR_SERVICE_KEY registered"

    if ($OpenfdaKey) {
        Set-RepoSecret "OPENFDA_API_KEY" $OpenfdaKey
        Write-Ok "OPENFDA_API_KEY registered"
    } else {
        Write-Info "OPENFDA_API_KEY skipped (collector runs in no-key mode)"
    }

    if ($BraveKey) {
        Set-RepoSecret "BRAVE_API_KEY" $BraveKey
        Write-Ok "BRAVE_API_KEY registered"
    } else {
        Write-Info "BRAVE_API_KEY skipped (ENABLE_SEARCH=false by default)"
    }

    if ($DataGoKey) {
        Set-RepoSecret "DATA_GO_KR_KEY" $DataGoKey
        Write-Ok "DATA_GO_KR_KEY registered"
    } else {
        Write-Info "DATA_GO_KR_KEY skipped (ENABLE_MOLEG_API=false by default)"
    }
} catch {
    Write-Err $_.Exception.Message
    Fail-And-Exit "Failed to register one or more secrets. Re-run after fixing."
}

Write-Host ""
Write-Info "Current secrets:"
Invoke-Native { gh secret list --repo "$ghUser/$RepoName" } | Out-Host

$NotionToken = $null
$DataGoServiceKey = $null
$OpenfdaKey = $null
$BraveKey = $null
$DataGoKey = $null
[System.GC]::Collect()

# ---- 6. Done ----
Write-Title "6. Setup complete"

Write-Ok "All steps succeeded."
Write-Host ""
Write-Host "Next steps (manual):"
Write-Host "  1) In Notion, connect the Integration to the 'Global Regulatory Monitor' parent page"
Write-Host "     (Notion -> parent page -> ... -> Connections -> add the integration)"
Write-Host ""
Write-Host "  2) Trigger a manual dry-run to verify:"
Write-Host "     $repoUrl/actions/workflows/grm-intake.yml"
Write-Host "     -> Run workflow -> dry_run: true"
Write-Host ""
Write-Host "  3) If dry-run is OK, run again with dry_run: false to write to Notion"
Write-Host ""
Write-Host "  4) Paste docs/prompts/GRM_Prompt_v15.6.md into your Claude Code Routine"
Write-Host ""
Write-Host "  5) Cron schedule: daily 18:17 UTC (03:17 KST next day)"
Write-Host "     Routine digest: Monday 07:30 KST"
Write-Host ""
Write-Info "Repo URL: $repoUrl"
Write-Info "Actions:  $repoUrl/actions"
Write-Info "Secrets:  $repoUrl/settings/secrets/actions"
