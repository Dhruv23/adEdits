$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    $ffmpegExe = Get-ChildItem -Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter "ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ffmpegExe) {
        $env:Path = "$env:Path;$($ffmpegExe.DirectoryName)"
    }
}

& "$root\.venv\Scripts\Activate.ps1"
Set-Location "$root\backend"

$url = "http://127.0.0.1:8000"

Start-Job -ScriptBlock {
    param($url)
    for ($i = 0; $i -lt 30; $i++) {
        try {
            Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1 | Out-Null
            Start-Process $url
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
} -ArgumentList $url | Out-Null

uvicorn server:app --reload
