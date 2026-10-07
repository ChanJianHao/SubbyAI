# Compile with the hash-pinned, publisher-signed Inno Setup distribution.
$ErrorActionPreference = 'Stop'
$subbyRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $subbyRoot
$subbyManifest = Get-Content -LiteralPath 'packaging/tools.json' -Raw | ConvertFrom-Json
$subbyTool = $subbyManifest.inno_setup
$subbyToolsDir = Join-Path $subbyRoot 'build/tools'
New-Item -ItemType Directory -Path $subbyToolsDir -Force | Out-Null
$subbyArchive = Join-Path $subbyToolsDir 'inno-setup.exe'
$subbyCompilerDir = Join-Path $subbyToolsDir 'inno'
$subbyCompiler = Join-Path $subbyCompilerDir 'ISCC.exe'

function Test-SubbyPublisher([string] $Path) {
    $subbySignature = Get-AuthenticodeSignature -LiteralPath $Path
    return $subbySignature.Status -eq 'Valid' -and
        $subbySignature.SignerCertificate.Subject -match 'CN=Pyrsys B\.V\.'
}

if (-not (Test-Path -LiteralPath $subbyCompiler)) {
    if (-not (Test-Path -LiteralPath $subbyArchive) -or
        (Get-FileHash -LiteralPath $subbyArchive -Algorithm SHA256).Hash -ne $subbyTool.sha256) {
        Invoke-WebRequest -Uri $subbyTool.url -OutFile $subbyArchive
    }
    if ((Get-FileHash -LiteralPath $subbyArchive -Algorithm SHA256).Hash -ne $subbyTool.sha256 -or
        -not (Test-SubbyPublisher $subbyArchive)) {
        throw 'The installer compiler failed its checksum or publisher signature check.'
    }
    $subbyArguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
        '/CURRENTUSER', '/NOICONS', ('/DIR="' + $subbyCompilerDir + '"'))
    $subbySetup = Start-Process -FilePath $subbyArchive -ArgumentList $subbyArguments `
        -WindowStyle Hidden -Wait -PassThru
    if ($subbySetup.ExitCode -ne 0) { throw 'Installer compiler setup failed.' }
}
if (-not (Test-SubbyPublisher $subbyCompiler)) {
    throw 'The installed compiler failed its publisher signature check.'
}
$subbyVersion = & './.venv/Scripts/python.exe' -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
if ($LASTEXITCODE -ne 0) { throw 'Could not read the application version.' }
& $subbyCompiler /Qp "/DAppVersion=$subbyVersion" 'packaging/windows/installer.iss'
if ($LASTEXITCODE -ne 0) { throw 'Windows installer compilation failed.' }
