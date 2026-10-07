@echo off
:: ============================================================
:: start_all.bat
::
:: Starts the WHOLE system with one double-click:
::   1) checks that the tablet is connected and visible;
::   2) starts the Appium server in a separate window;
::   3) starts the Telegram bot via run_bot_forever.bat
::      (so it already has crash-restart protection).
::
:: Run this file every time you turn on the laptop.
:: To make everything start automatically on boot: right-click
:: this file -> "Create shortcut", then press Win+R, type
:: shell:startup, press Enter, and drag the shortcut into that
:: folder.
:: ============================================================

cd /d "%~dp0"

echo ================================================
echo  Deploy NLC - starting the whole system
echo ================================================

echo.
echo [1/3] Checking that the tablet is connected via USB...
echo (turn it on / unlock it now if it isn't already -
echo  the script will wait for it)
adb wait-for-device
adb devices
echo.
echo Make sure the tablet above shows status "device"
echo (NOT "unauthorized" - if it shows "unauthorized",
echo  confirm the USB debugging prompt on the tablet screen
echo  and run this file again).
echo.
pause

echo.
echo [2/3] Starting the Appium server in a separate window...
start "Appium Server - DO NOT CLOSE" cmd /k appium

echo Waiting 15 seconds for the Appium server to fully start...
timeout /t 15 /nobreak

echo.
echo [3/3] Starting the Telegram bot (with auto-restart on crash)...
echo This window is now the "bot" window - keep it open while
echo the bot should be running.
echo.
call run_bot_forever.bat
