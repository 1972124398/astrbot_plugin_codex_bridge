param(
    [string]$ListenAddress = '127.0.0.1',
    [int]$Port = 6197
)
$ErrorActionPreference = 'Stop'
$bridgeRoot = Split-Path -Parent $PSScriptRoot
$bridgePython = Join-Path $bridgeRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $bridgePython)) {
    python -m venv (Join-Path $bridgeRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the Python environment.' }
}
& $bridgePython -m pip install -r (Join-Path $bridgeRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Failed to install dependencies.' }
& $bridgePython (Join-Path $bridgeRoot 'host_agent.py') --host $ListenAddress --port $Port --token-file (Join-Path $bridgeRoot 'data\host.token')
exit $LASTEXITCODE
