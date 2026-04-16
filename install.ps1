#Requires -Version 5.1
<#
.SYNOPSIS
    PhoneIDE IDE - One-line installer for Windows
.DESCRIPTION
    Installs Python 3, pip dependencies, clones repo. Works on Windows 10/11.
.EXAMPLE
    irm https://raw.githubusercontent.com/ctz168/ide/main/install.ps1 | iex
.EXAMPLE
    $env:PHONEIDE_INSTALL_DIR="C:\my-ide"; irm https://raw.githubusercontent.com/ctz168/ide/main/install.ps1 | iex
.EXAMPLE
    $env:PHONEIDE_AUTO_START="1"; irm https://raw.githubusercontent.com/ctz168/ide/main/install.ps1 | iex
#>

$ErrorActionPreference = "SilentlyContinue"

# ── Config ───────────────────────────────────────────────
$RepoUrl = "https://github.com/ctz168/ide.git"
$DefaultDir = "$env:USERPROFILE\phoneide-ide"
$InstallDir = if ($env:PHONEIDE_INSTALL_DIR) { $env:PHONEIDE_INSTALL_DIR.Replace('~', $env:USERPROFILE) } else { $DefaultDir }

function Write-Info($msg)  { Write-Host "  [*] $msg" -ForegroundColor Blue }
function Write-OK($msg)    { Write-Host "  [+] $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Fail($msg)  { Write-Host "  [-] $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "       PhoneIDE IDE Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Info "Platform: Windows"
Write-Info "Install dir: $InstallDir"
Write-Host ""

# ── Step 1: Install Python ──────────────────────────────
Write-Host "[1/3] Checking Python..." -ForegroundColor Blue

$Python = $null
foreach ($cmd in @("python3", "python", "py")) {
    $exe = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($exe) {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 8)) {
                $Python = $cmd
                break
            }
        }
    }
}

if (-not $Python) {
    Write-Info "Python 3.8+ not found, installing via winget..."
    
    # Try winget (Windows 10 1709+)
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Info "Installing Python via winget..."
        winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Info "Winget install done, refreshing PATH..."
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        }
    }
    
    # Re-check
    foreach ($cmd in @("python3", "python", "py")) {
        $exe = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($exe) {
            $ver = & $cmd --version 2>&1
            if ($ver -match "Python (\d+)\.(\d+)") {
                $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 8)) {
                    $Python = $cmd; break
                }
            }
        }
    }
    
    # Last resort: try the default install path
    if (-not $Python) {
        $pyExe = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
        if (Test-Path $pyExe) { $Python = $pyExe }
        else {
            $pyExe = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
            if (Test-Path $pyExe) { $Python = $pyExe }
        }
    }

    if (-not $Python) {
        Write-Fail "Python installation failed"
        Write-Host ""
        Write-Host "Please install Python 3.8+ manually:" -ForegroundColor Yellow
        Write-Host "  1. Download: https://www.python.org/downloads/" -ForegroundColor Yellow
        Write-Host "  2. Run installer and CHECK 'Add Python to PATH'" -ForegroundColor Yellow
        Write-Host "  3. Reopen terminal and re-run this script" -ForegroundColor Yellow
        exit 1
    }
}

$pyVer = & $Python --version 2>&1
Write-OK $pyVer

# ── Step 2: Install pip + dependencies ──────────────────
Write-Host ""
Write-Host "[2/3] Installing dependencies..." -ForegroundColor Blue

# Ensure pip
& $Python -m pip --version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Info "Installing pip..."
    # Try embedded pip first, then fallback
    & $Python -m ensurepip --upgrade 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        # Download get-pip.py
        $getPip = "$env:TEMP\get-pip.py"
        try {
            Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
            & $Python $getPip
            Remove-Item $getPip -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Warn "pip install failed"
        }
    }
}

# Install flask + flask-cors
& $Python -m pip install flask flask-cors --quiet 2>$null
if ($LASTEXITCODE -ne 0) {
    & $Python -m pip install --user flask flask-cors --quiet 2>$null
}
if ($LASTEXITCODE -eq 0) {
    Write-OK "flask + flask-cors"
} else {
    Write-Warn "pip install failed — try: $Python -m pip install flask flask-cors"
}

# ── Step 3: Clone & setup ──────────────────────────────
Write-Host ""
Write-Host "[3/3] Setting up PhoneIDE IDE..." -ForegroundColor Blue

if (Test-Path "$InstallDir\.git") {
    Write-Info "Updating existing installation..."
    Push-Location $InstallDir
    git pull --ff-only 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Warn "git pull failed — using existing files" }
    Pop-Location
} else {
    if (Test-Path $InstallDir) {
        Write-Warn "Directory $InstallDir exists but is not a git repo"
        $InstallDir = "$InstallDir-$(Get-Date -Format 'yyyyMMddHHmmss')"
        Write-Warn "Using $InstallDir instead"
    }
    
    Write-Info "Cloning ctz168/ide..."
    
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Write-Info "Installing git via winget..."
        winget install Git.Git --accept-package-agreements --accept-source-agreements 2>$null
        if ($LASTEXITCODE -eq 0) {
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        }
    }
    
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Write-Fail "git not found — please install Git: https://git-scm.com/download/win"
        exit 1
    }
    
    git clone --depth 1 $RepoUrl $InstallDir 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "git clone failed — check your network"
        exit 1
    }
}

# Create dirs
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\phoneide_workspace" | Out-Null
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.phoneide" | Out-Null

Write-OK "Ready at $InstallDir"

# ── Done ────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Installation complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Start server:" -ForegroundColor White
Write-Host "    cd $InstallDir" -ForegroundColor Cyan
Write-Host "    $Python server.py" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Then open: " -NoNewline -ForegroundColor White
Write-Host "http://localhost:1239" -ForegroundColor Blue
Write-Host ""

# Auto-launch
if ($env:PHONEIDE_AUTO_START) {
    Write-Host "Starting server..." -ForegroundColor Cyan
    Push-Location $InstallDir
    & $Python server.py
    Pop-Location
}
