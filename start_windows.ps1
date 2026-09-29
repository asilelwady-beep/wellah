$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = $null
$prefix = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    $python = 'py'
    $prefix = @('-3')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = 'python'
} else {
    Write-Host 'Python 3.10 or newer is required to run this local demo.'
    exit 1
}

$versionText = & $python @prefix --version 2>&1
if ($LASTEXITCODE -ne 0 -or "$versionText" -notmatch 'Python (\d+)\.(\d+)') {
    Write-Host 'Python 3.10 or newer is required to run this local demo.'
    exit 1
}
if ([int]$Matches[1] -lt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -lt 10)) {
    Write-Host 'Please update Python to version 3.10 or newer.'
    exit 1
}

$secure = Read-Host 'Choose a private admin password (10+ characters)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $env:WALLAHA_ADMIN_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    $secure.Dispose()
}
if ($env:WALLAHA_ADMIN_PASSWORD.Length -lt 10) {
    Write-Host 'The password must contain at least 10 characters.'
    exit 1
}

$env:WALLAHA_BIND = '127.0.0.1'
$env:WALLAHA_PORT = '8080'
Write-Host 'Open these links on this computer:'
Write-Host 'Admin:    http://127.0.0.1:8080/admin'
Write-Host 'Customer: http://127.0.0.1:8080/customer'
Write-Host 'Driver:   http://127.0.0.1:8080/driver'
Write-Host 'Admin phone: 01113887292'
Write-Host 'Admin username: owner'
Write-Host 'Keep this window open. Press Ctrl+C to stop the demo.'
Start-Process 'http://127.0.0.1:8080/admin'
try {
    & $python @prefix 'server.py'
} finally {
    Remove-Item Env:WALLAHA_ADMIN_PASSWORD -ErrorAction SilentlyContinue
}
