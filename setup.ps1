param([string]$Python = 'py')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path -LiteralPath '.venv/pyvenv.cfg') {
    $cfg = Get-Content -LiteralPath '.venv/pyvenv.cfg'
    $homeLine = $cfg | Where-Object { $_ -like 'home = *' } | Select-Object -First 1
    if ($homeLine -and -not (Test-Path -LiteralPath $homeLine.Substring(7))) {
        Write-Host 'Восстанавливаю перенесённое окружение установленным Python...'
        & $Python -m venv --upgrade --system-site-packages .venv
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось восстановить окружение. Укажите установленный Python через -Python' }
    }
} else {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать окружение Python' }
}
& '.venv/Scripts/python.exe' -m pip install -r requirements-integration.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости' }
& '.venv/Scripts/python.exe' -B -X utf8 -m gap_agent doctor
Write-Host 'Для настройки LLM прочитайте README.md; запуск: .\run-agent.ps1'
