$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (!(Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example. MOCK_AI=true works without an API key."
}

if (!(Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Host "Python was not found in PATH. Install Python 3.12+ or run inside Docker."
  exit 1
}

$BundledNodeDir = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin"
$BundledPnpm = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\bin\pnpm.cmd"
if (!(Get-Command node -ErrorAction SilentlyContinue)) {
  if (Test-Path (Join-Path $BundledNodeDir "node.exe")) {
    $env:PATH = "$BundledNodeDir;$env:PATH"
    Write-Host "Using bundled Node.js from Codex runtime."
  } else {
    Write-Host "Node.js was not found in PATH. Install Node 20+ or run inside Docker."
    exit 1
  }
}

function Get-BackendPidsOnPort {
  param([int]$Port)

  $Pids = @()
  try {
    $Pids += Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
      Select-Object -ExpandProperty OwningProcess
  } catch {
    $NetstatLines = netstat -ano | Select-String ":$Port\s+.*LISTENING"
    foreach ($Line in $NetstatLines) {
      $Parts = ($Line.Line -replace "^\s+", "") -split "\s+"
      if ($Parts.Length -ge 5) {
        $ParsedPid = 0
        if ([int]::TryParse($Parts[-1], [ref]$ParsedPid)) {
          $Pids += $ParsedPid
        }
      }
    }
  }

  $Pids | Sort-Object -Unique
}

function Stop-PreviousBackend {
  Get-BackendPidsOnPort -Port 8000 |
    ForEach-Object {
      Write-Host "Stopping process $_ currently listening on port 8000."
      Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }

  Get-Process python -ErrorAction SilentlyContinue |
    Where-Object {
      try {
        $_.Path -like "$Root\*" -and $_.ProcessName -like "python*"
      } catch {
        $false
      }
    } |
    ForEach-Object {
      Write-Host "Stopping previous local backend process $($_.Id)."
      Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}

Stop-PreviousBackend

$VenvPython = ".\.venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
  & $VenvPython -c "import sys" *> $null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Existing .venv cannot start; rebuilding it."
    Remove-Item ".venv" -Recurse -Force
  }
}

if (!(Test-Path ".venv")) {
  python -m venv .venv
}

& $VenvPython -c "import sys" *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Could not create a working Python virtual environment. Please reinstall Python 3.12+ or use Docker."
  exit 1
}

& $VenvPython -m pip install -r requirements.txt
Push-Location frontend
if (!(Test-Path "node_modules")) {
  if (Test-Path $BundledPnpm) {
    & $BundledPnpm install
  } elseif (Get-Command pnpm -ErrorAction SilentlyContinue) {
    pnpm install
  } else {
    npm install
  }
}
Pop-Location

$VenvPythonPath = (Resolve-Path $VenvPython).Path
$BackendLog = Join-Path $Root "backend.log"
if (Test-Path $BackendLog) {
  Remove-Item $BackendLog -Force
}
$BackendCommand = "cd '$Root'; & '$VenvPythonPath' -m uvicorn botc_ai.api.app:app --host 127.0.0.1 --port 8000 *>> '$BackendLog'"
Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $BackendCommand

$BackendReady = $false
for ($i = 0; $i -lt 30; $i++) {
  try {
    $Response = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8000/api/health" -TimeoutSec 1
    if ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500) {
      $BackendReady = $true
      break
    }
  } catch {
    Start-Sleep -Milliseconds 500
  }
}

if (!$BackendReady) {
  Write-Host "Backend did not start on http://127.0.0.1:8000."
  if (Test-Path $BackendLog) {
    Write-Host "Last backend log lines:"
    Get-Content $BackendLog -Tail 80
  }
  exit 1
}

Write-Host "Backend ready at http://127.0.0.1:8000."

Push-Location frontend
if (Test-Path $BundledPnpm) {
  & $BundledPnpm run dev
} elseif (Get-Command pnpm -ErrorAction SilentlyContinue) {
  pnpm run dev
} else {
  npm run dev
}
Pop-Location
