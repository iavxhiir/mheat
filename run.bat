@echo off
REM MHEAT — one-command entry point (Windows cmd / PowerShell).
REM
REM   run.bat                    :: env + lint + tests + reproduce + STAC (~5 min)
REM   run.bat --quick            :: lint + tests only                     (~2 min)
REM   run.bat --include-slow     :: + bench + docker build
REM   run.bat --list             :: every available phase
REM
REM Creates .venv\ on first run, installs backend dev deps, then calls
REM scripts\run_all.py. Subsequent runs reuse the venv and are fast.
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION
cd /d "%~dp0"

REM Locate Python.
where python >nul 2>nul
if errorlevel 1 (
  where py >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Python 3.11+ is required. Install it, then re-run run.bat
    exit /b 2
  )
  set "PY=py -3"
) else (
  set "PY=python"
)

set "VENV=.venv"
if not exist "%VENV%\" (
  echo [run.bat] First run -- creating %VENV% and installing backend dev deps...
  %PY% -m venv "%VENV%"
  if errorlevel 1 exit /b 2
)

call "%VENV%\Scripts\activate.bat"
if errorlevel 1 (
  echo ERROR: could not activate %VENV%
  exit /b 2
)

python -m pip install --upgrade pip --quiet

set "REQ=backend\requirements-dev.txt"
set "STAMP=%VENV%\.mheat-install-stamp"
set "NEED_INSTALL=0"
if not exist "%STAMP%" (
  set "NEED_INSTALL=1"
) else (
  for %%A in ("%REQ%") do set "REQ_TIME=%%~tA"
  for %%A in ("%STAMP%") do set "STAMP_TIME=%%~tA"
  if "!REQ_TIME!" GTR "!STAMP_TIME!" set "NEED_INSTALL=1"
)
if "%NEED_INSTALL%"=="1" (
  echo [run.bat] Installing %REQ% ...
  python -m pip install --quiet -r "%REQ%"
  if errorlevel 1 exit /b 2
  type nul > "%STAMP%"
)

python scripts\run_all.py %*
exit /b %errorlevel%
