param(
    [string]$Version = "v2.41.1-beta2.25.9"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Dist = Join-Path $Root "dist"
$SafeVersion = ($Version -replace '[^A-Za-z0-9._-]', '-')
$Name = "FIFA-14-Local-FUT-$SafeVersion"
$Stage = Join-Path ([System.IO.Path]::GetTempPath()) ($Name + "-" + [guid]::NewGuid().ToString("N"))
$StageRoot = Join-Path $Stage $Name
$ZipPath = Join-Path $Dist ($Name + ".zip")

$RepoOnly = @(
    ".git",
    ".github",
    ".venv",
    "venv",
    "artifacts",
    "reports",
    "logs",
    "captures",
    "runtime",
    "certs",
    "state",
    "dist"
)

$RepoDocs = @(
    ".gitignore",
    ".gitattributes",
    "SETUP_GITHUB_REPO.cmd",
    "PUSH_TO_GITHUB.cmd",
    "PACKAGE_RELEASE.cmd"
)

try {
    New-Item -ItemType Directory -Force -Path $Dist | Out-Null
    New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null

    Get-ChildItem -LiteralPath $Root -Force | ForEach-Object {
        if ($RepoOnly -contains $_.Name) { return }
        if ($RepoDocs -contains $_.Name) { return }

        $Destination = Join-Path $StageRoot $_.Name
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }

    $Packager = Join-Path $StageRoot "tools\package_release.ps1"
    if (Test-Path -LiteralPath $Packager) {
        Remove-Item -LiteralPath $Packager -Force
    }

    if (Test-Path -LiteralPath $ZipPath) {
        Remove-Item -LiteralPath $ZipPath -Force
    }

    Compress-Archive -LiteralPath $StageRoot -DestinationPath $ZipPath -CompressionLevel Optimal
    Write-Host "[OK] Release ZIP: $ZipPath"
}
finally {
    if (Test-Path -LiteralPath $Stage) {
        Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
