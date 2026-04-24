# clevernotes installer (Windows / Docker)
# Run in PowerShell: .\install.ps1
#
# Prereq: Docker Desktop installed and running. We don't auto-install Docker
# (it needs admin + WSL2 + a reboot) — we just check for it.

$ErrorActionPreference = "Stop"

$Image = if ($env:CLEVERNOTES_IMAGE) { $env:CLEVERNOTES_IMAGE } else { "002giriaryan/clevernotes:latest" }

function Step($n, $total, $msg) { Write-Host -NoNewline "[$n/$total] $msg" -ForegroundColor Cyan }
function Ok($msg)               { Write-Host " OK  $msg" -ForegroundColor Green }
function Warn($msg)             { Write-Host "!   $msg" -ForegroundColor Yellow }
function Fail($msg)             { Write-Host "X   $msg" -ForegroundColor Red; exit 1 }

$Total = 4
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "CleverNotes installer (Windows / Docker)" -ForegroundColor White
Write-Host ""

# ---- [1/4] Docker check ----
Step 1 $Total "Checking Docker Desktop .............."
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    Write-Host ""
    Fail "Docker not found on PATH. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and re-run."
}
& docker info > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Fail "Docker daemon is not reachable. Start Docker Desktop (wait for the whale icon to settle) and re-run."
}
Ok "docker available, daemon reachable"

# ---- [2/4] Pull image ----
Step 2 $Total "Pulling clevernotes image ............."
Write-Host ""
Write-Host "  Image: $Image" -ForegroundColor Gray
Write-Host "  First pull is ~2-3 GB (libreoffice + texlive). Subsequent runs are cached." -ForegroundColor Gray
& docker pull $Image
if ($LASTEXITCODE -ne 0) {
    Fail "docker pull failed. Check your internet, Docker Desktop status, and that the image '$Image' exists on Docker Hub."
}
Ok "pulled"

# ---- [3/4] API keys ----
Step 3 $Total "Configuring API keys .................."
Write-Host ""
Write-Host ""
Write-Host "CleverNotes uses Google AI Studio API keys. Each free-tier key is capped at" -ForegroundColor Gray
Write-Host "15 RPM / 1500 RPD, so using keys from DIFFERENT Google accounts draws from" -ForegroundColor Gray
Write-Host "independent quota pools. You'll enter up to THREE keys:" -ForegroundColor Gray
Write-Host "  1. GEMINI_API_KEY_STAGES12 - classification + grouping (stages 1+2)" -ForegroundColor Gray
Write-Host "  2. GEMINI_API_KEY_STAGE3   - notes generation (stage 3), primary" -ForegroundColor Gray
Write-Host "  3. GEMINI_API_KEY_STAGE3_BACKUP - stage 3 fallback (OPTIONAL)" -ForegroundColor Gray
Write-Host "     When the primary stage-3 key hits its daily quota, clevernotes" -ForegroundColor Gray
Write-Host "     transparently fails over to this backup and replays the chat" -ForegroundColor Gray
Write-Host "     history so cross-group context within a file is preserved." -ForegroundColor Gray
Write-Host "Get keys at: https://aistudio.google.com - click 'Get API key'." -ForegroundColor Gray
Write-Host "Keys will be shown as you type (not hidden)." -ForegroundColor Gray
Write-Host ""

$configDir = "$env:APPDATA\clevernotes"
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$configPath = Join-Path $configDir "config.env"

$key1 = $null
$key2 = $null
$key3 = ""
while ($true) {
    $key1 = Read-Host "Enter GEMINI_API_KEY_STAGES12 (stages 1 + 2)            "
    $key2 = Read-Host "Enter GEMINI_API_KEY_STAGE3  (stage 3 primary)          "
    $key3 = Read-Host "Enter GEMINI_API_KEY_STAGE3_BACKUP (blank to skip)      "

    if (-not $key1) {
        Warn "KEY 1 cannot be empty. Let's try again."
        Write-Host ""
        continue
    }

    if (-not $key2) {
        Warn "KEY 2 is empty - stage 3 will fall back to KEY 1."
        Write-Host "Both stages would then share the same free-tier pool (15 RPM / 1500 RPD)," -ForegroundColor Gray
        Write-Host "so effective throughput is halved. That's fine if it's intentional." -ForegroundColor Gray
        $ans = Read-Host "Proceed with KEY 1 for both pools? [Y/n]"
        if ($ans -match '^(n|N|no|No|NO)$') {
            Write-Host ""
            Write-Host "Let's re-enter the keys."
            Write-Host ""
            continue
        } else {
            $key2 = $key1
        }
    } elseif ($key1 -eq $key2) {
        Warn "KEY 1 and KEY 2 are identical."
        Write-Host "They'll share the same free-tier pool (15 RPM / 1500 RPD), so effective" -ForegroundColor Gray
        Write-Host "throughput is halved. This is fine if intentional - for best results you" -ForegroundColor Gray
        Write-Host "could use two keys from two different Google accounts (https://aistudio.google.com)." -ForegroundColor Gray
        $ans = Read-Host "Proceed with the same key for both, or re-enter them? [Y=proceed / n=re-enter]"
        if ($ans -match '^(n|N|no|No|NO)$') {
            Write-Host ""
            Write-Host "Let's re-enter the keys."
            Write-Host ""
            continue
        }
    }

    if ($key3 -and $key3 -eq $key2) {
        Warn "KEY 3 (stage 3 backup) is identical to KEY 2 (stage 3 primary)."
        Write-Host "Since both would draw from the same quota pool, the fallback would be dead" -ForegroundColor Gray
        Write-Host "on arrival. Dropping the backup - set it to a key from a DIFFERENT Google" -ForegroundColor Gray
        Write-Host "account for the failover to actually help." -ForegroundColor Gray
        $key3 = ""
    }
    break
}

# NOTE: no POPPLER_PATH here — that was only needed for the native Windows
# install. The Docker image ships poppler-utils on PATH.
$configBody = @"
# clevernotes config — edit manually or re-run install.ps1
GEMINI_API_KEY_STAGES12=$key1
GEMINI_API_KEY_STAGE3=$key2
# Optional: when the stage-3 primary hits its daily quota or a stubborn rate
# limit, clevernotes fails over to this key (chat history is replayed so
# cross-group context within a file is kept). Leave blank to disable.
GEMINI_API_KEY_STAGE3_BACKUP=$key3

# Model overrides (optional — defaults are gemma-4-26b-a4b-it for stages 1+2
# and gemma-4-31b-it for stage 3).
# GEMINI_MODEL_STAGE1=gemma-4-26b-a4b-it
# GEMINI_MODEL_STAGE2=gemma-4-26b-a4b-it
# GEMINI_MODEL_STAGE3=gemma-4-31b-it

# Pipeline tuning
# MAX_SLIDES_PER_GROUP=5
# MAX_GROUPS_PER_PPTX=120
"@
Set-Content -Path $configPath -Value $configBody -Encoding UTF8
Ok "saved to $configPath"

# ---- [4/4] launcher on PATH ----
Step 4 $Total "Installing clevernotes on PATH ........"
Write-Host ""
$binDir = Join-Path $env:LOCALAPPDATA "clevernotes\bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
Copy-Item -Path (Join-Path $ScriptDir "clevernotes.cmd") -Destination (Join-Path $binDir "clevernotes.cmd") -Force

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$binDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$binDir", "User")
    Warn "Added $binDir to your User PATH. Open a NEW terminal window for the change to take effect."
}
Ok "$binDir\clevernotes.cmd"

Write-Host ""
Write-Host "Installation complete." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Make sure Docker Desktop is running (whale icon in the system tray)."
Write-Host "  2. Create a folder anywhere, e.g. Desktop\blockchain-unit-3\"
Write-Host "  3. Put your lecture materials inside, named file_1.pptx, file_2.pptx, ... (PDFs also work, e.g. file_2.pdf)"
Write-Host "  4. cd into the folder and run: clevernotes"
Write-Host ""
Write-Host "The first run will be slower while Docker starts a fresh container;"
Write-Host "subsequent runs are near-instant. NOTES\ appears in your lecture folder"
Write-Host "directly (bind-mounted out of the container)."
Write-Host ""
