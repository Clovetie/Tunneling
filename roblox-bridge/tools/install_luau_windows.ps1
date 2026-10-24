# Fetch the official x64 Luau 0.744 Windows tools and verify the release archive.
# Run from PowerShell: .\tools\install_luau_windows.ps1
$ErrorActionPreference = 'Stop'
$Version = '0.744'
$Url = "https://github.com/luau-lang/luau/releases/download/$Version/luau-windows.zip"
$ExpectedSha256 = 'fde3e22c557aac6afe20a34e8050b7d8c290fa42638c04274a307ed5d0ef0db8'
$Tools = $PSScriptRoot
$Temp = Join-Path ([System.IO.Path]::GetTempPath()) ("arena-luau-" + [guid]::NewGuid().ToString('N'))
$Zip = Join-Path $Temp 'luau-windows.zip'
$Extract = Join-Path $Temp 'extract'
New-Item -ItemType Directory -Path $Temp, $Extract -Force | Out-Null
try {
    Write-Host "Downloading official Luau $Version Windows x64 release..."
    Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
    $ActualSha256 = (Get-FileHash -Path $Zip -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualSha256 -ne $ExpectedSha256) {
        throw "Luau archive SHA-256 mismatch. Expected $ExpectedSha256; got $ActualSha256"
    }
    Expand-Archive -Path $Zip -DestinationPath $Extract -Force
    foreach ($Name in @('luau-compile.exe', 'luau-analyze.exe')) {
        $Source = Get-ChildItem -Path $Extract -Filter $Name -Recurse | Select-Object -First 1
        if (-not $Source) { throw "Official archive did not contain $Name" }
        Copy-Item -Path $Source.FullName -Destination (Join-Path $Tools $Name) -Force
    }
    & (Join-Path $Tools 'luau-compile.exe') --help 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "luau-compile.exe failed to execute (exit $LASTEXITCODE)" }
    & (Join-Path $Tools 'luau-analyze.exe') --help 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "luau-analyze.exe failed to execute (exit $LASTEXITCODE)" }
    Write-Host 'Installed and checksum-verified luau-compile.exe + luau-analyze.exe.' -ForegroundColor Green
    Write-Host 'The bridge will discover these tools automatically on the next MCP request.'
}
finally {
    Remove-Item -Path $Temp -Recurse -Force -ErrorAction SilentlyContinue
}
