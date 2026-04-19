@echo off
:: Start Dynamic Radio daemon in a new minimized window
set "PROJECT_DIR=%~dp0..\.."
pushd "%PROJECT_DIR%"
start "Dynamic Radio" /min uv run dynamic-radio --log-level INFO
popd
