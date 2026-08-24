$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Nạp toàn bộ cấu hình từ .env vào process hiện tại trước khi khởi động Streamlit.
# Giá trị trong file được ưu tiên để không bị sót biến từ lần chạy tenant trước.
$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Khong tim thay file .env tai: $envFile"
}

Get-Content -LiteralPath $envFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
        return
    }
    $parts = $line -split "=", 2
    if ($parts.Count -ne 2 -or -not $parts[0].Trim()) {
        return
    }
    $name = $parts[0].Trim()
    $value = $parts[1].Trim()
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
}

Write-Host "Da nap cau hinh tu .env"
Write-Host "Tenant: $env:AI_CORE_UI_TENANT_ID | Config: $env:AI_CORE_UI_CONFIG_VERSION | Cache: $env:AI_CORE_SEMANTIC_CACHE_ENABLED"

Write-Host "H2-12 UI: http://localhost:8501"
Write-Host "Sale cung Wi-Fi: http://<IP-may-nay>:8501"
Write-Host "Xem feedback: python -m ai_core.feedback --tail 20"
Write-Host "Thong ke nguoi test: python -m ai_core.feedback --stats"

python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
