# Build every generated nomodsN module and collect the jars into .\dist
#
#   .\build.ps1                 # build all modules
#   .\build.ps1 -Clean          # ./gradlew clean build
#   .\build.ps1 -Task :nomods3:build   # build a single module
#
# Gradle itself must run on a JDK 21, so this script locates one and points
# JAVA_HOME at it for the duration of the build.

[CmdletBinding()]
param(
    [switch]$Clean,
    [string]$Task = 'build'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

function Find-Jdk21 {
    $candidates = [System.Collections.Generic.List[string]]::new()
    if ($env:JAVA_HOME) { $candidates.Add($env:JAVA_HOME) }

    $searchRoots = @(
        'C:\Program Files\Java',
        'C:\Program Files\Eclipse Adoptium',
        'C:\Program Files\Microsoft',
        'C:\Program Files\Zulu',
        "$env:LOCALAPPDATA\Programs\Eclipse Adoptium"
    )
    foreach ($dir in $searchRoots) {
        if (-not (Test-Path $dir)) { continue }
        Get-ChildItem $dir -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '21' } |
            Sort-Object Name -Descending |
            ForEach-Object { $candidates.Add($_.FullName) }
    }

    foreach ($candidate in $candidates) {
        $java = Join-Path $candidate 'bin\java.exe'
        if (-not (Test-Path $java)) { continue }

        # Prefer the "release" file: no need to launch java just to read a version.
        $releaseFile = Join-Path $candidate 'release'
        if (Test-Path $releaseFile) {
            if ((Get-Content $releaseFile -Raw) -match 'JAVA_VERSION="21') {
                return $candidate
            }
            continue
        }

        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $output = (& $java -version 2>&1 | Out-String)
        } finally {
            $ErrorActionPreference = $previous
        }
        if ($output -match 'version "21\.') {
            return $candidate
        }
    }
    return $null
}

$jdk = Find-Jdk21
if (-not $jdk) {
    throw 'JDK 21 not found. Install a JDK 21 and/or set JAVA_HOME to it.'
}

Write-Host "Using JDK: $jdk"
$env:JAVA_HOME = $jdk
$env:PATH = (Join-Path $jdk 'bin') + ';' + $env:PATH

$tasks = @()
if ($Clean) { $tasks += 'clean' }
$tasks += $Task

& (Join-Path $root 'gradlew.bat') @tasks --console=plain
if ($LASTEXITCODE -ne 0) {
    throw "Gradle build failed (exit code $LASTEXITCODE)."
}

# Note: the dist folder is shared with massbuild.py, so existing jars are kept
# and only the freshly built ones are overwritten.
$dist = Join-Path $root 'dist'
New-Item -ItemType Directory -Path $dist -Force | Out-Null

$jars = @(Get-ChildItem $root -Directory -Filter 'nomods*' | ForEach-Object {
    $libs = Join-Path $_.FullName 'build\libs'
    if (Test-Path $libs) {
        Get-ChildItem $libs -Filter '*.jar' |
            Where-Object { $_.Name -notmatch '-(sources|javadoc)\.jar$' }
    }
})

foreach ($jar in $jars) {
    Copy-Item $jar.FullName $dist -Force
}

if ($jars.Count -eq 0) {
    Write-Warning "No jars found. Did the '$Task' task actually build any module?"
} else {
    $totalBytes = ($jars | Measure-Object -Property Length -Sum).Sum
    if ($totalBytes -lt 1MB) {
        $size = "{0:N0} KB" -f ($totalBytes / 1KB)
    } else {
        $size = "{0:N1} MB" -f ($totalBytes / 1MB)
    }
    Write-Host ("Collected {0} jar(s) ({1}) into {2}" -f $jars.Count, $size, $dist)
}
