# Start the Fabrique workspace daemon on this computer (Windows).
#
# The daemon is what lets the agents work in the folders where you cloned your
# GitHub/GitLab projects: it lists folders for the picker, checks a project,
# writes the agents' files and runs commands there. It connects to the NATS
# server of the Docker stack (nats://localhost:4222, override with $env:NATS_URL)
# and keeps retrying until it is up.
#
# If scripts are blocked on this machine, run:
#   powershell -ExecutionPolicy Bypass -File .\scripts\start-daemon.ps1
#
# When the broker has one user per component (SECURITY.md, "Securing the
# bus"), give the daemon its credentials: $env:NATS_DAEMON_PASSWORD (user
# "daemon"), $env:NATS_USER and $env:NATS_PASSWORD, -Creds <file> (or
# $env:NATS_CREDS_FILE), or $env:NATS_NKEY_SEED; for TLS, $env:NATS_TLS = 'true'
# with $env:NATS_TLS_CA naming the CA that signed the broker's certificate.
param(
    [string]$Creds = ''
)
$ErrorActionPreference = 'Stop'
if ($Creds) { $env:NATS_CREDS_FILE = $Creds }

$Root = Split-Path -Parent $PSScriptRoot
$DaemonDir = Join-Path $Root 'vscode-daemon'
$Venv = Join-Path $DaemonDir '.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'

if (-not (Test-Path $VenvPython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv $Venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv $Venv
    } else {
        throw 'Python 3.9 or newer is required: install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH").'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
}

& $VenvPython -m pip install --quiet --disable-pip-version-check -r (Join-Path $DaemonDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the daemon requirements.' }
# A .creds file or an nkey seed signs the connection with nkeys.
if ($env:NATS_CREDS_FILE -or $env:NATS_NKEY_SEED) {
    & $VenvPython -m pip install --quiet --disable-pip-version-check 'nkeys==0.2.1'
    if ($LASTEXITCODE -ne 0) { throw 'Could not install nkeys.' }
}
# The broker's "daemon" user, from the same variable docker compose reads.
if (-not $env:NATS_USER -and $env:NATS_DAEMON_PASSWORD) {
    $env:NATS_USER = 'daemon'
    $env:NATS_PASSWORD = $env:NATS_DAEMON_PASSWORD
}

if (-not $env:NATS_URL) { $env:NATS_URL = 'nats://localhost:4222' }
& $VenvPython (Join-Path $DaemonDir 'daemon.py')
