@echo off
cd /d "%~dp0.."
git config core.hooksPath .githooks
if errorlevel 1 (
  echo Failed to set core.hooksPath
  exit /b 1
)
echo Git hooks enabled: .githooks/pre-push blocks direct pushes to dev and main.
