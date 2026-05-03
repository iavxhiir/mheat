@echo off
REM MHEAT — start the service locally (Windows).
REM
REM   start.bat              :: build frontend + run FastAPI on :8000 in DEMO_MODE
REM   start.bat --no-frontend :: API only
REM   start.bat --port 9000   :: custom port
REM
REM Stop with Ctrl+C.
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION
cd /d "%~dp0"

set "PORT=8000"
set "BUILD_FRONTEND=1"
:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--no-frontend" ( set "BUILD_FRONTEND=0" & shift & goto parse_args )
if /I "%~1"=="--port" ( set "PORT=%~2" & shift & shift & goto parse_args )
shift
goto parse_args
:args_done

where python >nul 2>nul
if errorlevel 1 (
  where py >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Python 3.11+ required.
    exit /b 2
  )
  set "PY=py -3"
) else (
  set "PY=python"
)

set "VENV=.venv"
if not exist "%VENV%\" (
  echo [start.bat] First run -- creating %VENV% and installing deps...
  %PY% -m venv "%VENV%"
  if errorlevel 1 exit /b 2
)
call "%VENV%\Scripts\activate.bat" || (echo ERROR: activate failed & exit /b 2)

python -m pip install --upgrade pip --quiet

set "REQ=backend\requirements-dev.txt"
set "STAMP=%VENV%\.mheat-install-stamp"
if not exist "%STAMP%" (
  echo [start.bat] Installing backend deps...
  python -m pip install --quiet -r "%REQ%"
  if errorlevel 1 exit /b 2
  type nul > "%STAMP%"
)

if "%BUILD_FRONTEND%"=="1" (
  where npm >nul 2>nul
  if not errorlevel 1 (
    if not exist "frontend\dist\" (
      echo [start.bat] Building frontend ^(Vite^)...
      pushd frontend
      call npm install --no-audit --no-fund --loglevel=error
      call npm run build
      popd
    )
    set "FRONTEND_DIR=%CD%\frontend\dist"
  )
)

if "%DEMO_MODE%"=="" set "DEMO_MODE=true"
echo.
echo ----------------------------------------------------------
echo   MHEAT starting (DEMO_MODE=%DEMO_MODE%)
echo     dashboard: http://localhost:%PORT%/
echo     API docs:  http://localhost:%PORT%/api/docs
echo     health:    http://localhost:%PORT%/api/health
echo     (Ctrl+C to stop)
echo ----------------------------------------------------------
echo.

python -m uvicorn app.main:app --host 0.0.0.0 --port %PORT% --app-dir backend --reload
endlocal
