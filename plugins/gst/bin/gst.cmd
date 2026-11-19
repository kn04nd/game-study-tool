@echo off
rem gst shim for cmd/PowerShell: prefers the uv-installed gst.exe, else runs this checkout with uv.
if exist "%USERPROFILE%\.local\bin\gst.exe" (
  "%USERPROFILE%\.local\bin\gst.exe" %*
  exit /b %ERRORLEVEL%
)
set VIRTUAL_ENV=
uv run --quiet --python 3.13 --project "%~dp0..\..\.." python -P -X utf8 -m gst %*
