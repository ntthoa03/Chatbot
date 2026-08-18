$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "H2-12 UI: http://localhost:8501"
Write-Host "Sale cung Wi-Fi: http://<IP-may-nay>:8501"
Write-Host "Xem feedback: python -m ai_core.feedback --tail 20"
Write-Host "Thong ke nguoi test: python -m ai_core.feedback --stats"

python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
