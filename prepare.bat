@echo off
REM MHEAT — prepare the submission bundle (Windows).
REM Superset of run.bat: every CI gate + every artefact regeneration.
REM Summary written to out\PREPARE_SUMMARY.md.

setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION
cd /d "%~dp0"

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
  echo [prepare.bat] First run -- creating %VENV% and installing deps...
  %PY% -m venv "%VENV%"
  if errorlevel 1 exit /b 2
)
call "%VENV%\Scripts\activate.bat" || (echo ERROR: activate failed & exit /b 2)
python -m pip install --upgrade pip --quiet

set "REQ=backend\requirements-dev.txt"
set "STAMP=%VENV%\.mheat-install-stamp"
if not exist "%STAMP%" (
  echo [prepare.bat] Installing backend deps...
  python -m pip install --quiet -r "%REQ%"
  if errorlevel 1 exit /b 2
  type nul > "%STAMP%"
)

python scripts\prepare_submission.py %*
exit /b %errorlevel%
