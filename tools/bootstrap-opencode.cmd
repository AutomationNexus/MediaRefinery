@echo off
setlocal
cd /d "%~dp0.."
if not exist "tooling\opencode" (
  echo ERROR: tooling\opencode seed not found
  exit /b 1
)
if not exist "opencode.json" (
  copy /Y "opencode.json.example" "opencode.json" >nul
  echo Created opencode.json from example
) else (
  echo opencode.json already exists; left unchanged.
)
if not exist ".opencode" mkdir ".opencode"
if exist ".opencode\agent" (
  powershell -NoProfile -Command "Remove-Item -LiteralPath '.opencode\agent' -Recurse -Force -ErrorAction SilentlyContinue"
)
if exist ".opencode\command" (
  powershell -NoProfile -Command "Remove-Item -LiteralPath '.opencode\command' -Recurse -Force -ErrorAction SilentlyContinue"
)
xcopy /E /I /Y "tooling\opencode" ".opencode" >nul
echo Bootstrapped .opencode from tooling\opencode
echo Run tools\install-githooks.cmd if git hooks are not configured
