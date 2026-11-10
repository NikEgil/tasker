@echo off
chcp 65001 >nul
title Tasker
cd /d "%~dp0"

set "PYTHON="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON=py -3"
if not defined PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON=python"
)

if not defined PYTHON (
    echo.
    echo Не найден Python 3.10 или новее.
    echo Установите Python с https://www.python.org/downloads/
    echo и отметьте "Add python.exe to PATH" при установке.
    echo.
    pause
    exit /b 1
)

%PYTHON% server.py %*
if errorlevel 1 (
    echo.
    echo Tasker завершился с ошибкой. Смотрите сообщение выше.
    pause
)
