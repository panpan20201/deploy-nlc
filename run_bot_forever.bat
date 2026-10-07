@echo off
:: ============================================================
:: run_bot_forever.bat
::
:: Safety net: keeps bot.py running at all times.
::
:: A plain "python bot.py" run stops for good if the bot
:: crashes for any reason (network error, unexpected exception,
:: etc.) - the Telegram bot just goes silent until someone
:: restarts it by hand.
::
:: This script runs bot.py in a LOOP instead: whenever the
:: process exits (crash or otherwise), it waits 10 seconds and
:: starts it again. The 10-second pause avoids a "runaway"
:: restart loop if the problem is not a one-off (e.g. wrong
:: token, no internet) - easier to notice in the console window
:: than hundreds of restarts per second.
::
:: Every start and exit code is logged to watchdog.log next to
:: this script, with a timestamp - if the bot was ever down,
:: you can check there when and how many times.
:: ============================================================

cd /d "%~dp0"
call venv\Scripts\activate.bat

:restart_loop
echo [%date% %time%] Starting bot.py... >> watchdog.log
echo [%date% %time%] Starting bot.py...

python bot.py

echo [%date% %time%] bot.py exited (code %errorlevel%). Restarting in 10 seconds... >> watchdog.log
echo.
echo bot.py exited (code %errorlevel%). Restarting in 10 seconds...
echo To stop for good, close this window.
echo.

timeout /t 10 /nobreak
goto restart_loop
