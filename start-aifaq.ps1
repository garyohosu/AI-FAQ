[CmdletBinding()]
param(
    [ValidateSet("Menu", "Admin", "Chat", "Doctor")]
    [string]$Mode = "Menu",

    [string]$Requester = $env:USERNAME,

    [switch]$Install,

    [switch]$Update
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Push-Location $repoRoot
try {
    if ($Update) {
        Write-Host "Updating repository..."
        & git pull --ff-only
        if ($LASTEXITCODE -ne 0) {
            throw "git pull failed."
        }
        $Install = $true
    }

    $venvDir = Join-Path $repoRoot ".venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    $aifaqExe = Join-Path $venvDir "Scripts\aifaq.exe"
    $adminExe = Join-Path $venvDir "Scripts\aifaq-admin.exe"

    if (-not (Test-Path $venvPython)) {
        Write-Host "Creating Python virtual environment..."

        $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($pyLauncher) {
            & py -3.12 -m venv $venvDir
        }
        else {
            $python = Get-Command python -ErrorAction SilentlyContinue
            if (-not $python) {
                throw "Python was not found. Install Python 3.12 or later."
            }
            & python -m venv $venvDir
        }

        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
            throw "Failed to create the virtual environment."
        }

        $Install = $true
    }

    if ($Install -or -not (Test-Path $aifaqExe) -or -not (Test-Path $adminExe)) {
        Write-Host "Installing AI-FAQ into the virtual environment..."
        & $venvPython -m pip install -e ".[dev]"
        if ($LASTEXITCODE -ne 0) {
            throw "Package installation failed. Close other AI-FAQ windows and try again."
        }
    }

    if ($Mode -eq "Menu") {
        Write-Host ""
        Write-Host "AI-FAQ Launcher"
        Write-Host "  1. IT administrator monitor"
        Write-Host "  2. User chat"
        Write-Host "  3. Environment check"
        Write-Host "  Q. Quit"
        Write-Host ""

        $choice = (Read-Host "Select").Trim().ToLowerInvariant()
        switch ($choice) {
            "1" { $Mode = "Admin" }
            "2" { $Mode = "Chat" }
            "3" { $Mode = "Doctor" }
            "q" { return }
            "quit" { return }
            default { throw "Unknown selection: $choice" }
        }
    }

    # 実行用exeではなくPythonモジュールを起動する。editable install後は、
    # git pullで更新されたソースをそのまま読み込めるため、日常更新のたびに
    # エントリーポイントexeを作り直す必要がない。
    switch ($Mode) {
        "Admin" {
            # 未回答が無いときも終了せず、質問が届くまで監視を続ける。
            & $venvPython -m aifaq.admin_monitor
        }
        "Chat" {
            if ([string]::IsNullOrWhiteSpace($Requester)) {
                $Requester = "user"
            }
            & $venvPython -m aifaq chat --requester $Requester
        }
        "Doctor" {
            & $venvPython -m aifaq doctor
        }
    }
}
catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    throw
}
finally {
    Pop-Location
}
