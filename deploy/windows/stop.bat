@echo off
:: Stop the Dynamic Radio daemon
echo Stopping Dynamic Radio...
taskkill /f /fi "WINDOWTITLE eq Dynamic Radio" >nul 2>&1
taskkill /f /im mpv.exe >nul 2>&1
echo Done.
