# Aqua Code installer for Windows.
#   irm https://raw.githubusercontent.com/saadvvsaadvv344-star/aqua-code/main/install.ps1 | iex
# Installs into %USERPROFILE%\AquaCode and adds a Desktop shortcut. Your keys, chats and workspace are never overwritten on update.
$ErrorActionPreference = 'Stop'
$repo = 'saadvvsaadvv344-star/aqua-code'
$dest = if ($env:AQUA_DIR) { $env:AQUA_DIR } else { Join-Path $env:USERPROFILE 'AquaCode' }

function Find-Python {
    foreach ($c in @('python', 'py')) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notmatch 'WindowsApps') {
            try { & $cmd.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"; if ($LASTEXITCODE -eq 0) { return $cmd.Source } } catch {}
        }
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    Write-Host "Aqua Code needs Python 3.9 or newer, and it was not found." -ForegroundColor Yellow
    Write-Host "Install it with:  winget install Python.Python.3.12   (or from https://www.python.org/downloads/), then run this again."
    return
}

Write-Host "Downloading Aqua Code..." -ForegroundColor Cyan
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("aqua-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $zip = Join-Path $tmp 'aqua.zip'
    Invoke-WebRequest -UseBasicParsing "https://github.com/$repo/archive/refs/heads/main.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem $tmp -Directory | Where-Object Name -like 'aqua-code-*' | Select-Object -First 1
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Get-ChildItem $src.FullName -Force | Where-Object { $_.Name -notin @('data', 'workspace') } | Copy-Item -Destination $dest -Recurse -Force
} finally { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }

# Desktop shortcut (no console window)
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py }
if (-not $env:AQUA_NO_SHORTCUT) {
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Aqua Code.lnk'))
$lnk.TargetPath = $pyw
$lnk.Arguments = '"' + (Join-Path $dest 'aqua_app.py') + '"'
$lnk.WorkingDirectory = $dest
$lnk.IconLocation = (Join-Path $dest 'assets\aqua.ico')
$lnk.Description = 'Aqua Code - your own coding agent'
$lnk.Save()
}

Write-Host "Installed in $dest and added 'Aqua Code' to your Desktop." -ForegroundColor Green
Write-Host "On first launch it asks for a free API key (Gemini, Groq, Mistral...) or lets you try without one."
if ($args -contains '-Run') { Start-Process $pyw -ArgumentList ('"' + (Join-Path $dest 'aqua_app.py') + '"') -WorkingDirectory $dest }
