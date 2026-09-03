@echo off
rem PAC CT launcher (Windows).
rem
rem Lives at the root of the install, beside `current`, `versions\` and
rem `userdata\`. `current` is a junction, so an update is a repointing and a
rem rollback is the same command with the previous version.
setlocal
set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

if exist "%HERE%\current\app.py" (
  set "VERSION_DIR=%HERE%\current"
  set "DATA_DIR=%HERE%\userdata"
) else if exist "%HERE%\app.py" (
  rem Straight out of the zip, before `--instalar` ran.
  set "VERSION_DIR=%HERE%"
  set "DATA_DIR=%HERE%"
) else (
  echo [ERRO] Nao encontrei "current" nem "app.py" em %HERE%.
  echo        Descompacte o pacote em PAC-CT\versions\^<versao^>\ e rode:
  echo        python PAC-CT\versions\^<versao^>\app.py --instalar
  exit /b 1
)

set "PACCT_ROOT=%VERSION_DIR%"
set "PACCT_DATA_DIR=%DATA_DIR%"

set "PY=%VERSION_DIR%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%VERSION_DIR%\app.py" --web %*
