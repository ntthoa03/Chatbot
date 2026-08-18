$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$ngrokCommand = Get-Command ngrok -ErrorAction SilentlyContinue
$localNgrok = Join-Path $PSScriptRoot "tools\ngrok\ngrok.exe"
if ($ngrokCommand) {
    $ngrokExe = $ngrokCommand.Source
}
elseif (Test-Path -LiteralPath $localNgrok) {
    $ngrokExe = $localNgrok
}
else {
    throw "Chua cai ngrok. Tai tai https://ngrok.com/download roi them ngrok.exe vao PATH."
}

if (-not $env:AI_CORE_UI_ACCESS_CODE) {
    $secureCode = Read-Host "Dat ma truy cap cho Sale" -AsSecureString
    $codePointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureCode)
    try {
        $env:AI_CORE_UI_ACCESS_CODE = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($codePointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($codePointer)
    }
}

if ($env:AI_CORE_UI_ACCESS_CODE.Length -lt 8) {
    throw "Ma truy cap phai co it nhat 8 ky tu."
}

try {
    $existingHealth = Invoke-WebRequest `
        -Uri "http://127.0.0.1:8501/_stcore/health" `
        -UseBasicParsing `
        -TimeoutSec 2
    if ($existingHealth.StatusCode -eq 200) {
        throw "Cong 8501 dang co UI khac chay. Hay dung UI cu truoc khi mo ngrok."
    }
}
catch {
    if ($_.Exception.Message -like "Cong 8501*") {
        throw
    }
}

$stdoutPath = Join-Path $PSScriptRoot ".codex_tmp\h2_12_ngrok_ui.stdout.log"
$stderrPath = Join-Path $PSScriptRoot ".codex_tmp\h2_12_ngrok_ui.stderr.log"
$pythonExe = (Get-Command python).Source
$streamlit = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList @(
        "-m", "streamlit", "run", "app.py",
        "--server.address", "127.0.0.1",
        "--server.port", "8501",
        "--server.headless", "true"
    ) `
    -WorkingDirectory $PSScriptRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-WebRequest `
                -Uri "http://127.0.0.1:8501/_stcore/health" `
                -UseBasicParsing `
                -TimeoutSec 2
            if ($health.StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch {
            # Cho Streamlit khoi dong.
        }
    }
    if (-not $ready) {
        throw "Streamlit khong khoi dong duoc. Xem log: $stderrPath"
    }

    Write-Host "UI da duoc bao ve bang ma truy cap."
    Write-Host "Giu cua so nay mo trong thoi gian Sale test; nhan Ctrl+C de dung."
    & $ngrokExe http 8501
}
finally {
    if ($streamlit -and -not $streamlit.HasExited) {
        Stop-Process -Id $streamlit.Id -Force
    }
}
