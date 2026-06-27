# Bootstrap local OpenCode config from committed seeds.
# Usage: powershell -File tools/bootstrap-opencode.ps1 [-Force]

param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$example = Join-Path $root "opencode.json.example"
$liveJson = Join-Path $root "opencode.json"
$seed = Join-Path $root "tooling\opencode"
$live = Join-Path $root ".opencode"

if (-not (Test-Path $seed)) {
    Write-Error "Missing seed directory: $seed"
}

if (-not (Test-Path $liveJson)) {
    Copy-Item $example $liveJson
    Write-Host "Created opencode.json from example."
} elseif ($Force) {
    Copy-Item $example $liveJson -Force
    Write-Host "Refreshed opencode.json from example (-Force)."
} else {
    $exampleObj = Get-Content $example -Raw | ConvertFrom-Json
    $liveObj = Get-Content $liveJson -Raw | ConvertFrom-Json
    $liveObj.default_agent = $exampleObj.default_agent
    $liveObj.model = $exampleObj.model
    $liveObj.small_model = $exampleObj.small_model
    $liveObj.agent = $exampleObj.agent
    foreach ($key in @("shell", "instructions", "share", "snapshot", "watcher", "tool_output", "compaction", "permission")) {
        if ($exampleObj.PSObject.Properties.Name -contains $key) {
            $liveObj | Add-Member -NotePropertyName $key -NotePropertyValue $exampleObj.$key -Force
        }
    }
    $liveObj | ConvertTo-Json -Depth 30 | Set-Content -Path $liveJson -Encoding utf8
    Write-Host "Updated opencode.json agent defaults from example (permissions preserved)."
}

New-Item -ItemType Directory -Force -Path $live | Out-Null
$agentLive = Join-Path $live "agent"
$commandLive = Join-Path $live "command"
$agentSeed = Join-Path $seed "agent"
$commandSeed = Join-Path $seed "command"

New-Item -ItemType Directory -Force -Path $agentLive | Out-Null
New-Item -ItemType Directory -Force -Path $commandLive | Out-Null

Copy-Item -Path (Join-Path $seed "*.md") -Destination $live -Force
if (Test-Path $agentSeed) {
    Copy-Item -Path (Join-Path $agentSeed "*") -Destination $agentLive -Force
    Get-ChildItem $agentLive -File | ForEach-Object {
        if (-not (Test-Path (Join-Path $agentSeed $_.Name))) {
            Remove-Item $_.FullName -Force
            Write-Host "Removed stale agent: $($_.Name)"
        }
    }
}
if (Test-Path $commandSeed) {
    Copy-Item -Path (Join-Path $commandSeed "*") -Destination $commandLive -Force
    Get-ChildItem $commandLive -File | ForEach-Object {
        if (-not (Test-Path (Join-Path $commandSeed $_.Name))) {
            Remove-Item $_.FullName -Force
            Write-Host "Removed stale command: $($_.Name)"
        }
    }
}

Write-Host "Synced tooling/opencode -> .opencode"

$hooks = git -C $root config --get core.hooksPath 2>$null
if (-not $hooks) {
    Write-Host "Run tools\install-githooks.cmd once to block direct pushes to dev/main."
} else {
    Write-Host "Git hooks path: $hooks"
}
