@echo off
REM MHEAT — single all-in-one entry point (Windows).
REM
REM Runs env check + venv bootstrap + install + every gate + every
REM artefact regeneration + optional Docker build + optional server
REM start, all on one command.
REM
REM Flags:
REM   --fast         skip slow steps (bench + docker)
REM   --skip-audits  skip pip-audit + npm audit
REM   --no-install   reuse existing .venv / node_modules
REM   --keep-going   don't stop at first failing gate
REM   --serve        at the end, start the service (blocks until Ctrl+C)
REM   --help         this help

setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION
cd /d "%~dp0"

set "FAST=0"
set "NO_INSTALL=0"
set "SERVE=0"
set "PASSTHROUGH="

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--help" goto show_help
if /I "%~1"=="-h" goto show_help
if /I "%~1"=="--fast" ( set "FAST=1" & shift & goto parse_args )
if /I "%~1"=="--no-install" ( set "NO_INSTALL=1" & shift & goto parse_args )
if /I "%~1"=="--serve" ( set "SERVE=1" & shift & goto parse_args )
if /I "%~1"=="--skip-audits" ( set "PASSTHROUGH=!PASSTHROUGH! --skip-audits" & shift & goto parse_args )
if /I "%~1"=="--keep-going" ( set "PASSTHROUGH=!PASSTHROUGH! --keep-going" & shift & goto parse_args )
set "PASSTHROUGH=!PASSTHROUGH! %~1"
shift
goto parse_args
:args_done
if "%FAST%"=="0" set "PASSTHROUGH=!PASSTHROUGH! --include-slow"

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

echo.
echo ============================================================
echo   MHEAT -- all-in-one runner
echo ============================================================
%PY% --version
where node >nul 2>nul && node --version
where npm  >nul 2>nul && npm --version
where docker >nul 2>nul && docker --version

set "VENV=.venv"
if "%NO_INSTALL%"=="0" (
  if not exist "%VENV%\" (
    echo.
    echo [mheat.bat] Creating %VENV% and installing deps (first run)...
    %PY% -m venv "%VENV%"
    if errorlevel 1 exit /b 2
  )
)
call "%VENV%\Scripts\activate.bat" || (echo ERROR: activate failed & exit /b 2)
python -m pip install --upgrade pip --quiet

if "%NO_INSTALL%"=="0" (
  set "REQ=backend\requirements-dev.txt"
  set "STAMP=%VENV%\.mheat-install-stamp"
  if not exist "!STAMP!" (
    echo.
    echo [mheat.bat] Installing backend deps...
    python -m pip install --quiet -r "!REQ!"
    if errorlevel 1 exit /b 2
    type nul > "!STAMP!"
  )
)

echo.
echo ============================================================
echo   Running every gate + regenerating every artefact
echo ============================================================
python scripts\prepare_submission.py %PASSTHROUGH%
set "PREP_EXIT=%errorlevel%"

REM Docker (only if daemon is reachable)
set "DOCKER_NOTE=skipped -- docker daemon not reachable"
if "%FAST%"=="0" (
  where docker >nul 2>nul
  if not errorlevel 1 (
    docker info >nul 2>nul
    if not errorlevel 1 (
      echo.
      echo ============================================================
      echo   Docker build
      echo ============================================================
      docker build -t mheat:local .
      if not errorlevel 1 set "DOCKER_NOTE=built mheat:local"
    )
  )
)
echo.
echo Docker: %DOCKER_NOTE%

if "%PREP_EXIT%"=="0" (
  echo.
  echo ============================================================
  echo   Everything green. Summary at out\PREPARE_SUMMARY.md
  echo ============================================================
  if "%SERVE%"=="1" (
    echo Starting the service ^(blocks until Ctrl+C^)...
    call start.bat
    exit /b %errorlevel%
  )
  echo.
  echo   To start the service now:
  echo     start.bat                 :: DEMO_MODE on, http://localhost:8000
  echo     set DEMO_MODE=false ^&^& start.bat  :: live mode ^(needs Copernicus creds^)
  echo.
  exit /b 0
)

echo.
echo Some gates failed. See the tail above and out\PREPARE_SUMMARY.md.
exit /b %PREP_EXIT%

:show_help
echo.
echo MHEAT -- single all-in-one entry point.
echo.
echo   mheat.bat                 :: full run ^(every gate + every artefact + docker^)
echo   mheat.bat --fast          :: skip bench + docker
echo   mheat.bat --skip-audits   :: offline ^(no pip-audit / npm audit^)
echo   mheat.bat --no-install    :: reuse existing .venv / node_modules
echo   mheat.bat --keep-going    :: don't stop at first failing gate
echo   mheat.bat --serve         :: at the end, start the service
echo   mheat.bat --help          :: this help
exit /b 0
