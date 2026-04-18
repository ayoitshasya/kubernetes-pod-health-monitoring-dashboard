@echo off
setlocal enabledelayedexpansion
title K8s Pod Health Dashboard - Live Demo Setup

:: ============================================================
::  K8s Pod Health Dashboard - Windows 11 Live Demo Setup
::  Double-click this file or run from CMD to use.
::
::  Commands:
::    demo_setup.bat          -> full setup + start everything
::    demo_setup.bat --demo   -> deploy crash pod only
::    demo_setup.bat --stop   -> stop backend + minikube
::    demo_setup.bat --status -> check what's running
:: ============================================================

:: Enable ANSI colors (Windows 10 1511+ / Windows 11)
reg query HKCU\Console /v VirtualTerminalLevel >nul 2>&1
if errorlevel 1 (
    reg add HKCU\Console /v VirtualTerminalLevel /t REG_DWORD /d 1 /f >nul 2>&1
)

set "ESC="
set "RESET=%ESC%[0m"
set "BOLD=%ESC%[1m"
set "GREEN=%ESC%[92m"
set "RED=%ESC%[91m"
set "YELLOW=%ESC%[93m"
set "BLUE=%ESC%[94m"
set "CYAN=%ESC%[96m"
set "DIM=%ESC%[90m"

set PORT=5000
set BACKEND_SCRIPT=k8s_backend.py
set FRONTEND_FILE=k8s_dashboard.html

:: ── route by argument ──────────────────────────────────────
if "%1"=="--demo"   goto :run_demo
if "%1"=="--stop"   goto :run_stop
if "%1"=="--status" goto :run_status
goto :run_setup

:: =============================================================
::  FULL SETUP  (9 steps)
:: =============================================================
:run_setup
call :print_banner
call :check_files
call :check_python
call :check_docker
call :check_minikube
call :start_minikube
call :enable_metrics_server
call :deploy_pods
call :install_python_deps
call :patch_frontend_url
call :start_backend
call :open_dashboard
call :print_done
goto :eof

:: =============================================================
::  DEPLOY CRASH POD ONLY  (run just before presenting)
:: =============================================================
:run_demo
call :print_banner
echo %CYAN%  Deploying CrashLoopBackOff demo pod...%RESET%
echo.
kubectl delete pod crash-demo --ignore-not-found=true >nul 2>&1
timeout /t 2 /nobreak >nul
kubectl run crash-demo --image=busybox --restart=Always -- /bin/sh -c "exit 1"
if errorlevel 1 (
    call :err "Failed to deploy crash-demo. Is Minikube running?"
    echo.
    echo %YELLOW%  Check status first: %CYAN%demo_setup.bat --status%RESET%
    echo.
    pause
    goto :eof
)
echo.
call :ok "crash-demo pod deployed!"
echo.
echo %YELLOW%  Watch restarts climb:  %CYAN%kubectl get pod crash-demo --watch%RESET%
echo %YELLOW%  View crash logs:       %CYAN%kubectl logs crash-demo%RESET%
echo %YELLOW%  Delete and respawn:    %CYAN%kubectl delete pod crash-demo%RESET%
echo.
pause
goto :eof

:: =============================================================
::  STOP EVERYTHING
:: =============================================================
:run_stop
call :print_banner
echo %CYAN%  Stopping services...%RESET%
echo.

:: Kill backend on port 5000
set KILLED=0
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":%PORT% " 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
    set KILLED=1
)
if "%KILLED%"=="1" (
    call :ok "Backend stopped (port %PORT%)"
) else (
    call :warn "Backend was not running"
)

:: Stop minikube
echo %DIM%  Stopping Minikube...%RESET%
minikube stop >nul 2>&1
if errorlevel 1 (
    call :warn "Minikube was not running"
) else (
    call :ok "Minikube stopped"
)
echo.
pause
goto :eof

:: =============================================================
::  STATUS CHECK
:: =============================================================
:run_status
call :print_banner
echo %BOLD%  Current Status%RESET%
echo.

:: Python
where python >nul 2>&1
if errorlevel 1 (
    call :warn "Python:         NOT found in PATH"
) else (
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do call :ok "Python:         %%v"
)

:: Docker
docker info >nul 2>&1
if errorlevel 1 (
    call :warn "Docker Desktop: NOT running"
) else (
    call :ok   "Docker Desktop: running"
)

:: Minikube
minikube status 2>nul | findstr "Running" >nul
if errorlevel 1 (
    call :warn "Minikube:       NOT running"
) else (
    call :ok   "Minikube:       running"
    echo.
    kubectl get nodes 2>nul
    echo.
    echo %DIM%  Pods:%RESET%
    kubectl get pods 2>nul
)

:: metrics-server
kubectl get deployment metrics-server -n kube-system >nul 2>&1
if errorlevel 1 (
    call :warn "metrics-server: NOT enabled  (run: minikube addons enable metrics-server)"
) else (
    call :ok   "metrics-server: enabled"
)

:: Backend
netstat -aon | findstr ":%PORT% " >nul 2>&1
if errorlevel 1 (
    call :warn "Backend:        NOT running on port %PORT%"
) else (
    call :ok   "Backend:        running on http://localhost:%PORT%"
)

echo.
pause
goto :eof

:: =============================================================
::  STEPS
:: =============================================================

:print_banner
cls
echo.
echo %CYAN%  +================================================+%RESET%
echo %CYAN%  ^|  K8s Pod Health Dashboard                    ^|%RESET%
echo %CYAN%  ^|  Windows 11 Live Demo Setup                  ^|%RESET%
echo %CYAN%  +================================================+%RESET%
echo.
goto :eof

:: ── [1] Check files ──────────────────────────────────────────
:check_files
echo %BLUE%[1/9] Checking project files...%RESET%
set MISSING=0
if not exist "%BACKEND_SCRIPT%" (
    call :err "Missing: %BACKEND_SCRIPT%"
    set MISSING=1
)
if not exist "%FRONTEND_FILE%" (
    call :err "Missing: %FRONTEND_FILE%"
    set MISSING=1
)
if not exist "requirements.txt" (
    call :err "Missing: requirements.txt"
    set MISSING=1
)
if "%MISSING%"=="1" (
    echo.
    echo %RED%  Run this script from the folder containing all project files.%RESET%
    echo.
    pause
    exit /b 1
)
call :ok "All project files found"
goto :eof

:: ── [2] Check Python ─────────────────────────────────────────
:check_python
echo %BLUE%[2/9] Checking Python...%RESET%
where python >nul 2>&1
if errorlevel 1 (
    call :err "Python not found in PATH."
    echo.
    echo %YELLOW%  Install Python 3.8+ and tick 'Add Python to PATH':%RESET%
    echo %CYAN%  https://www.python.org/downloads/%RESET%
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do call :ok "%%v found"
goto :eof

:: ── [3] Check Docker ─────────────────────────────────────────
:check_docker
echo %BLUE%[3/9] Checking Docker Desktop...%RESET%
docker info >nul 2>&1
if errorlevel 1 (
    call :err "Docker Desktop is not running."
    echo.
    echo %YELLOW%  Start Docker Desktop and wait for the whale icon in the%RESET%
    echo %YELLOW%  system tray to show 'Engine running', then re-run this script.%RESET%
    echo.
    echo %CYAN%  Download: https://www.docker.com/products/docker-desktop/%RESET%
    echo.
    pause
    exit /b 1
)
call :ok "Docker Desktop is running"
goto :eof

:: ── [4] Check Minikube installed ─────────────────────────────
:check_minikube
echo %BLUE%[4/9] Checking Minikube...%RESET%
where minikube >nul 2>&1
if errorlevel 1 (
    call :err "minikube not found in PATH."
    echo.
    echo %CYAN%  Download: https://minikube.sigs.k8s.io/docs/start/%RESET%
    echo.
    pause
    exit /b 1
)
call :ok "minikube found"
goto :eof

:: ── [5] Start Minikube ───────────────────────────────────────
:start_minikube
echo %BLUE%[5/9] Starting Minikube (docker driver)...%RESET%

minikube status 2>nul | findstr "Running" >nul
if not errorlevel 1 (
    call :ok "Minikube already running"
    goto :eof
)

echo %DIM%  Starting cluster - this takes 1-3 minutes on first run...%RESET%
minikube start --driver=docker
if errorlevel 1 (
    echo.
    call :err "Minikube failed to start."
    echo.
    echo %YELLOW%  Try:%RESET%
    echo %CYAN%    minikube delete%RESET%
    echo %CYAN%    minikube start --driver=docker%RESET%
    echo.
    pause
    exit /b 1
)
call :ok "Minikube started successfully"
goto :eof

:: ── [6] Enable metrics-server ────────────────────────────────
:enable_metrics_server
echo %BLUE%[6/9] Enabling metrics-server addon...%RESET%

:: Check if already enabled
kubectl get deployment metrics-server -n kube-system >nul 2>&1
if not errorlevel 1 (
    call :ok "metrics-server already enabled"
    goto :eof
)

minikube addons enable metrics-server
if errorlevel 1 (
    call :warn "Could not enable metrics-server — memory will show 0Mi"
    echo %DIM%  You can enable it manually later: minikube addons enable metrics-server%RESET%
) else (
    call :ok "metrics-server enabled"
    echo %DIM%  Note: metrics take ~60s to become available after first enable%RESET%
)
goto :eof

:: ── [7] Deploy pods ──────────────────────────────────────────
:deploy_pods
echo %BLUE%[7/9] Deploying demo pods...%RESET%

kubectl create deployment api-server --image=nginx  --replicas=2 >nul 2>&1
kubectl create deployment db-replica --image=redis  --replicas=1 >nul 2>&1
kubectl create deployment cache-svc  --image=busybox -- sleep 3600 >nul 2>&1
kubectl run crash-demo --image=busybox --restart=Always -- /bin/sh -c "exit 1" >nul 2>&1

call :ok "Pods deployed: api-server x2, db-replica, cache-svc, crash-demo"
echo %DIM%  Pods take ~30s to reach Running / CrashLoopBackOff status%RESET%
goto :eof

:: ── [8] Install Python deps ──────────────────────────────────
:install_python_deps
echo %BLUE%[8/9] Installing Python dependencies...%RESET%
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    call :err "pip install failed."
    echo %CYAN%  Make sure Python is installed: https://www.python.org/downloads/%RESET%
    pause
    exit /b 1
)
call :ok "Python dependencies installed"
goto :eof

:: ── Patch frontend URL ───────────────────────────────────────
:patch_frontend_url
powershell -Command "(Get-Content '%FRONTEND_FILE%') -replace 'const API_URL = null', 'const API_URL = ''http://localhost:%PORT%/api/pods''' | Set-Content '%FRONTEND_FILE%'"
call :ok "API_URL set in %FRONTEND_FILE%"
goto :eof

:: ── [9] Start backend ────────────────────────────────────────
:start_backend
echo %BLUE%[9/9] Starting Flask backend...%RESET%

:: Kill anything already on port 5000
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":%PORT% " 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

:: Start backend in a new window so it stays open
start "K8s Backend" cmd /k "python %BACKEND_SCRIPT%"
timeout /t 4 /nobreak >nul

netstat -aon | findstr ":%PORT% " >nul 2>&1
if errorlevel 1 (
    call :warn "Backend may still be starting — check the 'K8s Backend' window"
) else (
    call :ok "Backend running on http://localhost:%PORT%"
)
goto :eof

:: ── Open dashboard ───────────────────────────────────────────
:open_dashboard
timeout /t 1 /nobreak >nul
start "" "%FRONTEND_FILE%"
call :ok "Dashboard opened in browser"
goto :eof

:: ── Done ─────────────────────────────────────────────────────
:print_done
echo.
echo %GREEN%  +================================================+%RESET%
echo %GREEN%  ^|  Setup complete! Dashboard is live.          ^|%RESET%
echo %GREEN%  +================================================+%RESET%
echo.
echo %BOLD%  Demo commands:%RESET%
echo.
echo   %CYAN%demo_setup.bat --demo%RESET%     Deploy fresh crash pod before presenting
echo   %CYAN%demo_setup.bat --status%RESET%   Check Docker / Minikube / backend
echo   %CYAN%demo_setup.bat --stop%RESET%     Stop backend + Minikube when done
echo.
echo %BOLD%  Useful kubectl commands:%RESET%
echo.
echo   %DIM%kubectl get pods --watch%RESET%
echo   %DIM%kubectl top pods%RESET%
echo   %DIM%kubectl delete pod crash-demo%RESET%
echo   %DIM%kubectl scale deployment api-server --replicas=4%RESET%
echo   %DIM%kubectl logs crash-demo%RESET%
echo.
pause
goto :eof

:: =============================================================
::  UTILITY FUNCTIONS
:: =============================================================
:ok
echo   %GREEN%[OK]%RESET%  %~1
goto :eof

:warn
echo   %YELLOW%[!!]%RESET%  %~1
goto :eof

:err
echo   %RED%[XX]%RESET%  %~1
goto :eof
