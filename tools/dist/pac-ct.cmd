@echo off
rem PAC CT launcher (Windows).
rem
rem Lives at the root of the install, beside `current`, `versions\` and
rem `userdata\`. `current` is a junction, so an update is a repointing and a
rem rollback is the same command with the previous version.
rem
rem COM argumentos e' o que sempre foi: repassa tudo para o `app.py --web`,
rem entao `pac-ct.cmd --port 9000` e `pac-ct.cmd --atualizar` continuam
rem valendo como o INSTALAR.txt sempre disse. SEM argumentos abre um menu.
rem
rem O menu e' burro de proposito: le uma tecla e chama o `app.py`. Tudo que
rem pode dar errado -- copiar o pacote, conferir o sha256, trocar o `current`
rem -- esta' do lado do Python, onde a suite alcanca. Aqui so' fica o que um
rem `.cmd` sabe fazer sozinho: escolher e lembrar.
setlocal enabledelayedexpansion
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

rem Com argumentos: exatamente o que este arquivo sempre fez.
if not "%~1"=="" (
  "%PY%" "%VERSION_DIR%\app.py" --web %*
  exit /b %ERRORLEVEL%
)

rem --- configuracoes do lancador -------------------------------------------
rem Um `chave=valor` por linha, ao lado dos dados do usuario: em uma
rem instalacao versionada isso e' `userdata\`, que uma atualizacao nunca toca,
rem e na pasta portatil e' a propria pasta, onde a troca de arquivos so' move
rem os caminhos que ela sabe nomear. Sobrevive aos dois jeitos de atualizar.
set "CFG=%DATA_DIR%\launcher.cfg"
set "AUTO_CHECK=1"
if exist "%CFG%" (
  for /f "usebackq tokens=1,2 delims==" %%a in ("%CFG%") do (
    if /i "%%a"=="auto_check" set "AUTO_CHECK=%%b"
  )
) else (
  if not exist "%DATA_DIR%" mkdir "%DATA_DIR%" >nul 2>&1
  > "%CFG%" echo auto_check=1
)

set "UPD="
if "%AUTO_CHECK%"=="1" (
  echo Verificando atualizacoes...
  "%PY%" "%VERSION_DIR%\app.py" --verificar
  if !ERRORLEVEL! EQU 10 set "UPD=  ^<-- ha versao nova"
  echo.
)

:menu
cls
echo.
echo   PAC CT
echo   ======
echo.
echo     1^) Rodar o programa
echo     2^) Preparar esta pasta (portatil)
echo     3^) Instalar em %%LOCALAPPDATA%%\PAC-CT
echo     4^) Verificar atualizacoes
echo     5^) Atualizar!UPD!
echo     6^) Configuracoes
echo     0^) Sair
echo.
set "OPC="
set /p "OPC=  Opcao: "

if "%OPC%"=="1" goto rodar
if "%OPC%"=="2" goto preparar
if "%OPC%"=="3" goto instalar_local
if "%OPC%"=="4" goto verificar
if "%OPC%"=="5" goto atualizar
if "%OPC%"=="6" goto config
if "%OPC%"=="0" exit /b 0
goto menu

:rodar
echo.
echo Abra http://localhost:8765/ no navegador. Ctrl+C encerra.
"%PY%" "%VERSION_DIR%\app.py" --web
goto fim

:preparar
echo.
rem Qualquer chamada monta o venv a partir de `vendor/` antes de responder,
rem entao pedir a versao E' preparar a pasta -- sem rede e sem subir servidor.
echo Preparando esta pasta (venv a partir de vendor\, sem internet)...
"%PY%" "%VERSION_DIR%\app.py" --versao
echo.
echo Pronto. A opcao 1 ja' roda daqui.
goto fim

:instalar_local
echo.
"%PY%" "%VERSION_DIR%\app.py" --instalar-em "%LOCALAPPDATA%\PAC-CT"
goto fim

:verificar
echo.
"%PY%" "%VERSION_DIR%\app.py" --verificar
if !ERRORLEVEL! EQU 10 (set "UPD=  ^<-- ha versao nova") else (set "UPD=")
goto fim

:atualizar
echo.
"%PY%" "%VERSION_DIR%\app.py" --atualizar
set "UPD="
goto fim

:config
cls
echo.
echo   Configuracoes
echo   =============
echo.
if "%AUTO_CHECK%"=="1" (set "EST=ligado") else (set "EST=desligado")
echo     1^) Verificar atualizacoes ao abrir: !EST!
echo     0^) Voltar
echo.
set "OPC="
set /p "OPC=  Opcao: "
if "%OPC%"=="1" (
  if "%AUTO_CHECK%"=="1" (set "AUTO_CHECK=0") else (set "AUTO_CHECK=1")
  > "%CFG%" echo auto_check=!AUTO_CHECK!
  goto config
)
goto menu

:fim
echo.
pause
goto menu
