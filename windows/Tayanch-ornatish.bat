@echo off
setlocal
chcp 65001 >nul
title Tayanch
cd /d "%~dp0.." || goto nofolder
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto havepy
python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto havepy
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if defined PY goto havepy
goto nopython

:havepy
%PY% install.py %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" goto finished
echo  O'rnatish xato bilan tugadi. Yuqoridagi xabarni o'qing.
echo  Internet ulanganini tekshiring va faylni qaytadan ishga tushiring.
echo  Yordam: loyiha papkasidagi OQING.md fayli.
:finished
echo.
pause
exit /b %RC%

:nopython
echo.
echo  XATO: Python 3.11 yoki undan yangi versiya topilmadi.
echo.
echo   1. https://www.python.org/downloads/ saytidan Python 3.13 ni yuklab oling.
echo   2. O'rnatishda "Add python.exe to PATH" katagini belgilang.
echo   3. Microsoft Store dagi Python emas, python.org dagi o'rnatuvchidan foydalaning.
echo   4. Shu oynani yoping va faylni qaytadan ishga tushiring.
echo.
pause
exit /b 1

:nofolder
echo.
echo  XATO: loyiha papkasiga o'tib bo'lmadi.
echo  Papkani oddiy diskka ko'chiring, masalan C:\Tayanch , va qaytadan urinib ko'ring.
echo  Tarmoq papkasi yoki ZIP arxiv ichidan ishga tushirib bo'lmaydi.
echo.
pause
exit /b 1
