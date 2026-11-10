# SPDX-License-Identifier: Apache-2.0
# Builds the MousePods L2CAP driver with the WDK from NuGet, runs the compiler's
# static analysis (/analyze), InfVerif and Inf2Cat, test-signs the package with
# a self-made certificate and writes a Markdown report (failing lines first).
#
# Used by .github/workflows/windows-driver.yml; also runs on a Windows machine
# with Visual Studio 2022 (C++ workload) and nuget.exe on PATH.
#
#   pwsh windows/driver/build.ps1 [-OutDir out] [-Title "local build"]
#
# Output in OutDir: report.md, status.txt (success or failure), driver.zip
# (MousePodsL2cap.sys/.inf/.cat/.cer/.pdb) when the build got that far.

param(
    [string]$OutDir = (Join-Path $PSScriptRoot 'out'),
    [string]$Title = 'local build',
    [string]$Configuration = 'Release'
)

$ErrorActionPreference = 'Continue'
$null = New-Item -ItemType Directory -Force $OutDir
$OutDir = (Resolve-Path $OutDir).Path
Set-Location $PSScriptRoot
$pkg = Join-Path $OutDir 'pkg'
Remove-Item -Recurse -Force $pkg -ErrorAction SilentlyContinue
$null = New-Item -ItemType Directory -Force $pkg
Remove-Item -Force (Join-Path $OutDir 'driver.zip') -ErrorAction SilentlyContinue

$inActions = [bool]$env:GITHUB_ACTIONS
$steps = [System.Collections.Generic.List[object]]::new()
$failing = [System.Collections.Generic.List[string]]::new()
$compilerWarnings = [System.Collections.Generic.List[string]]::new()
$analyzeFindings = [System.Collections.Generic.List[string]]::new()
$outputs = [ordered]@{}

function Escape-Annotation([string]$text) {
    return $text.Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
}

function Annotate([string]$level, [string]$title, [string]$line) {
    if ($inActions) {
        Write-Host "::$level title=$(Escape-Annotation $title)::$(Escape-Annotation $line)"
    }
}

# Runs a native command, keeps its output, records the step.
function Invoke-Step([string]$name, [string]$exe, [string[]]$arguments) {
    Write-Host "== $name"
    Write-Host "$exe $($arguments -join ' ')"
    $lines = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" })
    $code = $LASTEXITCODE
    $lines | ForEach-Object { Write-Host $_ }
    $outputs[$name] = $lines
    $steps.Add([pscustomobject]@{ Name = $name; Ok = ($code -eq 0); Detail = "exit code $code" })
    if ($code -ne 0) {
        $failing.Add("$name failed (exit code $code)")
        Annotate 'error' $name "$name failed (exit code $code)"
    }
    return ($code -eq 0)
}

function Skip-Step([string]$name, [string]$why) {
    $steps.Add([pscustomobject]@{ Name = $name; Ok = $false; Detail = "not run: $why" })
}

function Find-Tool([string]$pattern) {
    $hit = Get-ChildItem -Path (Join-Path $PSScriptRoot 'packages') -Recurse -File -Filter $pattern.Split('\')[-1] -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like "*$pattern" } | Sort-Object FullName -Descending | Select-Object -First 1
    if ($hit) { return $hit.FullName }
    return $null
}

# 1. WDK and SDK from NuGet
$ok = Invoke-Step 'nuget restore' 'nuget' @('restore', 'packages.config', '-PackagesDirectory', 'packages', '-NonInteractive')

# 2. Build with /analyze
$built = $false
if ($ok) {
    # The WDK ships its build tasks for one MSBuild major version only
    # (Microsoft.DriverKit.Build.Tasks.<major>.0.dll): pick the matching Visual Studio.
    $tasks = Find-Tool 'bin\Microsoft.DriverKit.Build.Tasks.1*.0.dll'
    $major = 18
    if ($tasks -and ((Split-Path $tasks -Leaf) -match 'Tasks\.(\d+)\.0\.dll$')) { $major = [int]$Matches[1] }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    $msbuild = & $vswhere -latest -products * -version "[$major.0,$($major + 1).0)" -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
    $instances = @(& $vswhere -all -products * -format value -property installationPath)
    if (-not $msbuild) {
        $failing.Add("MSBuild $major not found (the WDK needs it); installed: $($instances -join '; ')")
        Skip-Step 'msbuild' "MSBuild $major not found"
    } else {
        $steps.Add([pscustomobject]@{ Name = 'Visual Studio'; Ok = $true; Detail = "$msbuild (installed: $($instances -join '; '))" })
        $built = Invoke-Step 'msbuild' $msbuild @(
            'MousePodsL2cap.sln', '/m', '/nologo', '/t:Rebuild',
            "/p:Configuration=$Configuration", '/p:Platform=x64', '/p:RunCodeAnalysis=true',
            '/v:minimal', '/clp:NoSummary;ForceNoAlign')
        $seen = @{}
        foreach ($line in $outputs['msbuild']) {
            $text = $line.Trim()
            if ($seen.ContainsKey($text)) { continue }
            $seen[$text] = $true
            if ($text -match ':\s+(fatal )?error\s+\w+') {
                $failing.Add($text)
                Annotate 'error' 'build error' $text
            } elseif ($text -match ':\s+warning\s+C(\d+)') {
                $code = [int]$Matches[1]
                if (($code -ge 6000 -and $code -lt 7000) -or ($code -ge 26000 -and $code -lt 34000)) {
                    $analyzeFindings.Add($text)
                    Annotate 'warning' '/analyze' $text
                } else {
                    $compilerWarnings.Add($text)
                    Annotate 'warning' 'compiler' $text
                }
            } elseif ($text -match ':\s+warning\s+\w+') {
                $compilerWarnings.Add($text)
                Annotate 'warning' 'build' $text
            }
        }
        if ($analyzeFindings.Count -gt 0) {
            $failing.Add("/analyze reported $($analyzeFindings.Count) finding(s)")
        }
    }
} else {
    Skip-Step 'msbuild' 'restore failed'
}

# 3. Collect the package
$sys = Get-ChildItem -Path (Join-Path $PSScriptRoot "x64\$Configuration") -Recurse -File -Filter 'MousePodsL2cap.sys' -ErrorAction SilentlyContinue | Select-Object -First 1
$haveFiles = $false
if ($sys) {
    $infs = @(Get-ChildItem -Path (Join-Path $PSScriptRoot "x64\$Configuration") -Recurse -File -Filter 'MousePodsL2cap.inf' -ErrorAction SilentlyContinue)
    $inf = $infs | Where-Object { $_.DirectoryName -eq $sys.DirectoryName } | Select-Object -First 1
    if (-not $inf) { $inf = $infs | Select-Object -First 1 }
    $pdb = Get-ChildItem -Path (Join-Path $PSScriptRoot "x64\$Configuration") -Recurse -File -Filter 'MousePodsL2cap.pdb' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($inf) {
        Copy-Item $sys.FullName, $inf.FullName $pkg
        if ($pdb) { Copy-Item $pdb.FullName $pkg }
        $haveFiles = $true
        $steps.Add([pscustomobject]@{ Name = 'package'; Ok = $true; Detail = "$($sys.FullName), $($inf.FullName)" })
    } else {
        $failing.Add('stamped MousePodsL2cap.inf not found in the build output')
        $steps.Add([pscustomobject]@{ Name = 'package'; Ok = $false; Detail = 'no .inf in the build output' })
    }
} else {
    Skip-Step 'package' 'no MousePodsL2cap.sys'
}

# 4. InfVerif /w (Windows driver rules)
$infOk = $false
if ($haveFiles) {
    $infverif = Find-Tool 'x64\infverif.exe'
    if ($infverif) {
        $infOk = Invoke-Step 'InfVerif /w' $infverif @('/w', '/v', (Join-Path $pkg 'MousePodsL2cap.inf'))
        foreach ($line in $outputs['InfVerif /w']) {
            if ($line -match '\b(ERROR|WARNING)\(') {
                $failing.Add("InfVerif: $($line.Trim())")
                Annotate ($(if ($line -match 'ERROR\(') { 'error' } else { 'warning' })) 'InfVerif' $line.Trim()
                $infOk = $false
            }
        }
    } else {
        $failing.Add('infverif.exe not found in the WDK package')
        Skip-Step 'InfVerif /w' 'infverif.exe not found'
    }
} else {
    Skip-Step 'InfVerif /w' 'no package'
}

# 5. Test signing: self-made certificate, sign .sys, catalog, sign .cat
$signed = $false
if ($haveFiles) {
    $signtool = Find-Tool 'x64\signtool.exe'
    $inf2cat = Find-Tool 'x86\Inf2Cat.exe'
    if (-not $signtool -or -not $inf2cat) {
        $failing.Add("signtool or Inf2Cat not found (signtool: $signtool, Inf2Cat: $inf2cat)")
        Skip-Step 'signing' 'tools not found'
    } else {
        $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject 'CN=MousePods Test Signing' `
            -CertStoreLocation 'Cert:\CurrentUser\My' -KeyAlgorithm RSA -KeyLength 2048 `
            -HashAlgorithm SHA256 -NotAfter (Get-Date).AddYears(5)
        $null = Export-Certificate -Cert $cert -FilePath (Join-Path $pkg 'MousePodsL2cap.cer')
        $sysOk = Invoke-Step 'signtool .sys' $signtool @('sign', '/fd', 'SHA256', '/sha1', $cert.Thumbprint, '/s', 'My', (Join-Path $pkg 'MousePodsL2cap.sys'))
        $catOk = Invoke-Step 'Inf2Cat' $inf2cat @("/driver:$pkg", '/os:10_X64', '/uselocaltime')
        $catSigned = $false
        if ($catOk) {
            $catSigned = Invoke-Step 'signtool .cat' $signtool @('sign', '/fd', 'SHA256', '/sha1', $cert.Thumbprint, '/s', 'My', (Join-Path $pkg 'MousePodsL2cap.cat'))
        } else {
            Skip-Step 'signtool .cat' 'Inf2Cat failed'
        }
        Remove-Item "Cert:\CurrentUser\My\$($cert.Thumbprint)" -ErrorAction SilentlyContinue
        $signed = $sysOk -and $catOk -and $catSigned
    }
} else {
    Skip-Step 'signing' 'no package'
}

# 6. driver.zip
if ($signed) {
    Compress-Archive -Path (Join-Path $pkg '*') -DestinationPath (Join-Path $OutDir 'driver.zip') -Force
    $zipped = Get-ChildItem (Join-Path $pkg '*') | ForEach-Object { $_.Name }
    $steps.Add([pscustomobject]@{ Name = 'driver.zip'; Ok = $true; Detail = ($zipped -join ', ') })
} else {
    Skip-Step 'driver.zip' 'package not complete'
}

# Report
$success = $built -and $haveFiles -and $infOk -and $signed -and $analyzeFindings.Count -eq 0 -and $failing.Count -eq 0
$status = if ($success) { 'success' } else { 'failure' }

$md = [System.Collections.Generic.List[string]]::new()
$md.Add("# Windows driver build: $Title")
$md.Add('')
$md.Add("Status: **$status**")
$md.Add('')
$md.Add("Compiler warnings: $($compilerWarnings.Count). /analyze findings: $($analyzeFindings.Count). InfVerif: $(if ($infOk) { 'clean' } else { 'not clean' }).")
$md.Add('')
$md.Add('## Failing lines')
$md.Add('')
if ($failing.Count -eq 0) { $md.Add('None.') } else { $md.Add('```'); $failing | ForEach-Object { $md.Add($_) }; $md.Add('```') }
$md.Add('')
$md.Add('## Steps')
$md.Add('')
$md.Add('| Step | Result | Detail |')
$md.Add('|---|---|---|')
foreach ($s in $steps) { $md.Add("| $($s.Name) | $(if ($s.Ok) { 'ok' } else { 'FAILED' }) | $($s.Detail.Replace('|', '/')) |") }
$md.Add('')
$md.Add("## /analyze findings ($($analyzeFindings.Count))")
$md.Add('')
if ($analyzeFindings.Count -eq 0) { $md.Add('None.') } else { $md.Add('```'); $analyzeFindings | ForEach-Object { $md.Add($_) }; $md.Add('```') }
$md.Add('')
$md.Add("## Compiler warnings ($($compilerWarnings.Count))")
$md.Add('')
if ($compilerWarnings.Count -eq 0) { $md.Add('None.') } else { $md.Add('```'); $compilerWarnings | ForEach-Object { $md.Add($_) }; $md.Add('```') }
foreach ($name in @('InfVerif /w', 'Inf2Cat', 'signtool .sys', 'signtool .cat', 'nuget restore')) {
    if ($outputs.Contains($name)) {
        $md.Add('')
        $md.Add("## $name output")
        $md.Add('')
        $md.Add('```')
        $outputs[$name] | Select-Object -Last 80 | ForEach-Object { $md.Add($_) }
        $md.Add('```')
    }
}
if ($outputs.Contains('msbuild')) {
    $md.Add('')
    $md.Add('## msbuild output (last 150 lines)')
    $md.Add('')
    $md.Add('```')
    $outputs['msbuild'] | Select-Object -Last 150 | ForEach-Object { $md.Add($_) }
    $md.Add('```')
}

Set-Content -Path (Join-Path $OutDir 'report.md') -Value $md -Encoding utf8NoBOM
Set-Content -Path (Join-Path $OutDir 'status.txt') -Value $status -Encoding ascii
Write-Host "status: $status"
if (-not $success) { exit 1 }
exit 0
