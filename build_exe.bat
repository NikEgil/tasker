@echo off
chcp 65001 >nul
setlocal
title Tasker - сборка Tasker.exe
cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "VENV=%TEMP%\tasker-build-venv"
set "WORK=%TEMP%\tasker-build-work"
set "SPEC=%TEMP%\tasker-build-spec"

set "PYTHON="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON=py -3"
if not defined PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON (
    echo.
    echo Не найден Python 3.10+. Установите Python с python.org.
    echo.
    if not defined TASKER_NO_PAUSE pause
    exit /b 1
)

if exist "%VENV%\Scripts\python.exe" goto venv_ready

echo [1/3] Создаю локальное окружение сборки...
%PYTHON% -m venv "%VENV%"
if errorlevel 1 goto error

:venv_ready
echo [2/3] Устанавливаю PyInstaller...
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet --upgrade "pyinstaller==6.22.3"
if errorlevel 1 goto error

echo [3/3] Собираю Tasker.exe...
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name Tasker --icon "%ROOT%\assets\tasker.ico" --version-file "%ROOT%\assets\version_info.txt" --add-data "%ROOT%\web;web" --distpath "%ROOT%" --workpath "%WORK%" --specpath "%SPEC%" "%ROOT%\server.py"
if errorlevel 1 goto error
if not exist "%ROOT%\Tasker.exe" goto error

echo.
echo Готово: %ROOT%\Tasker.exe
for %%A in ("%ROOT%\Tasker.exe") do echo Размер: %%~zA байт
echo.
if not defined TASKER_NO_PAUSE pause
exit /b 0

:error
echo.
echo Сборка не удалась. Проверьте сообщения выше.
if not defined TASKER_NO_PAUSE pause
exit /b 1
