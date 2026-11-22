param([Parameter(ValueFromRemainingArguments=$true)][string[]]$AgentArgs)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Нет локального окружения. Сначала выполните .\setup.ps1'
}
if (-not $AgentArgs -or $AgentArgs.Count -eq 0) { $AgentArgs = @('chat') }
& $pythonPath -B -X utf8 -m gap_agent @AgentArgs
exit $LASTEXITCODE
