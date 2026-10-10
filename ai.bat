@echo off
title Odysseus Environment

echo.
echo ========================================
echo   Starting Ollama
echo ========================================
start "Ollama" powershell.exe -NoExit -Command "& 'C:\Users\hpvan\AppData\Local\Programs\Ollama\ollama.exe' serve"

timeout /t 3 /nobreak >nul

echo.
echo ========================================
echo   Starting SearXNG
echo ========================================
start "SearXNG" powershell.exe -NoExit -Command "Set-Location 'C:\Users\hpvan\Apps\searxng-windows\scripts'; & '.\run.ps1'"

timeout /t 3 /nobreak >nul

echo.
echo ========================================
echo   Starting Odysseus
echo ========================================
start "Odysseus" powershell.exe -NoExit -Command "Set-Location 'C:\Users\hpvan\odysseus-main'; & '.\launch-windows.ps1' -BindHost 0.0.0.0"

echo.
echo ========================================
echo   Startup commands sent
echo ========================================
pause