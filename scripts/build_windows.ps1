param([string]$Python = '.local\build-venv\Scripts\python.exe', [string]$Iscc = '.local\tooling\InnoSetup\ISCC.exe')
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $root
& $Python -m PyInstaller --noconfirm packaging\pa_agent.spec
if ($LASTEXITCODE -ne 0) { throw 'Portable build failed' }
Set-Content -LiteralPath dist\PA-Agent\portable.flag -Value 'Keep runtime data in the adjacent data directory.' -Encoding utf8
& $Iscc packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
& $Python scripts\release_manifest.py
if ($LASTEXITCODE -ne 0) { throw 'Release archive failed' }
