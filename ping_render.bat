@echo off
powershell -NoProfile -WindowStyle Hidden -Command "try { Invoke-WebRequest -Uri 'https://normwear-shop.onrender.com/healthz' -TimeoutSec 20 -UseBasicParsing | Out-Null } catch { }"
