@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.." || goto failed
type nul > ".reseed"
if errorlevel 1 goto failed
echo.
echo  Buyruq berildi: namoyish bazasi 1-3 daqiqada yangilanadi.
echo  Tayanch ishlab turgan bo'lsa - o'zi yangilaydi.
echo  Ishlamayotgan bo'lsa - keyingi ishga tushirishda yangilanadi.
echo.
pause
exit /b 0

:failed
echo.
echo  XATO: loyiha papkasiga yozib bo'lmadi.
echo.
pause
exit /b 1
