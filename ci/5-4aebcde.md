# Windows driver build: run 5, commit 4aebcde

Status: **success**

Compiler warnings: 0. /analyze findings: 0. InfVerif: clean.

## Failing lines

None.

## Steps

| Step | Result | Detail |
|---|---|---|
| nuget restore | ok | exit code 0 |
| Visual Studio | ok | C:\Program Files\Microsoft Visual Studio\18\Enterprise\MSBuild\Current\Bin\amd64\MSBuild.exe (installed: C:\Program Files\Microsoft Visual Studio\18\Enterprise) |
| msbuild | ok | exit code 0 |
| package | ok | D:\a\mousepods\mousepods\windows\driver\x64\Release\MousePodsL2cap.sys, D:\a\mousepods\mousepods\windows\driver\x64\Release\MousePodsL2cap.inf |
| InfVerif /w | ok | exit code 0 |
| signtool .sys | ok | exit code 0 |
| Inf2Cat | ok | exit code 0 |
| signtool .cat | ok | exit code 0 |
| driver.zip | ok | mousepodsl2cap.cat, MousePodsL2cap.cer, MousePodsL2cap.inf, MousePodsL2cap.pdb, MousePodsL2cap.sys |

## /analyze findings (0)

None.

## Compiler warnings (0)

None.

## InfVerif /w output

```
Running in Verbose
Running Windows Driver INF check


Validating MousePodsL2cap.inf
INF is VALID

Checked 1 INF(s) in 0 m 0 s 2 ms
```

## Inf2Cat output

```
..........................
Signability test complete.

Errors:
None

Warnings:
None

Catalog generation complete.
D:\a\mousepods\mousepods\windows\driver\out\pkg\mousepodsl2cap.cat
```

## signtool .sys output

```
Done Adding Additional Store
Successfully signed: D:\a\mousepods\mousepods\windows\driver\out\pkg\MousePodsL2cap.sys

```

## signtool .cat output

```
Done Adding Additional Store
Successfully signed: D:\a\mousepods\mousepods\windows\driver\out\pkg\mousepodsl2cap.cat

```

## nuget restore output

```
Restoring NuGet package Microsoft.Windows.SDK.CPP.x64.10.0.28000.2526.
Restoring NuGet package Microsoft.Windows.SDK.CPP.10.0.28000.2526.
Restoring NuGet package Microsoft.Windows.WDK.x64.10.0.28000.2526.
  GET https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.cpp/10.0.28000.2526/microsoft.windows.sdk.cpp.10.0.28000.2526.nupkg
  GET https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.cpp.x64/10.0.28000.2526/microsoft.windows.sdk.cpp.x64.10.0.28000.2526.nupkg
  GET https://api.nuget.org/v3-flatcontainer/microsoft.windows.wdk.x64/10.0.28000.2526/microsoft.windows.wdk.x64.10.0.28000.2526.nupkg
  OK https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.cpp/10.0.28000.2526/microsoft.windows.sdk.cpp.10.0.28000.2526.nupkg 3ms
  OK https://api.nuget.org/v3-flatcontainer/microsoft.windows.wdk.x64/10.0.28000.2526/microsoft.windows.wdk.x64.10.0.28000.2526.nupkg 11ms
  OK https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.cpp.x64/10.0.28000.2526/microsoft.windows.sdk.cpp.x64.10.0.28000.2526.nupkg 13ms
Installed Microsoft.Windows.SDK.CPP.x64 10.0.28000.2526 from https://api.nuget.org/v3/index.json to C:\Users\runneradmin\.nuget\packages\microsoft.windows.sdk.cpp.x64\10.0.28000.2526 with content hash RzracWZEvKCZAFzkvfeVKmrI84PAt145iDyt2kB4rrLr4v/uqHJqTnm8wHIgB9iF/4s1bM6eAXsmiRsOO2/yrQ==.
Adding package 'Microsoft.Windows.SDK.CPP.x64.10.0.28000.2526' to folder 'D:\a\mousepods\mousepods\windows\driver\packages'
Added package 'Microsoft.Windows.SDK.CPP.x64.10.0.28000.2526' to folder 'D:\a\mousepods\mousepods\windows\driver\packages'
Installed Microsoft.Windows.WDK.x64 10.0.28000.2526 from https://api.nuget.org/v3/index.json to C:\Users\runneradmin\.nuget\packages\microsoft.windows.wdk.x64\10.0.28000.2526 with content hash q44V9dYa01f4iqSp6snB9uFhZ7WOoVhh6JSeRFN9887J2onboA8PCu314XMnKaUoEjbs+VXtif8XlS1Eklj1CQ==.
Adding package 'Microsoft.Windows.WDK.x64.10.0.28000.2526' to folder 'D:\a\mousepods\mousepods\windows\driver\packages'
Added package 'Microsoft.Windows.WDK.x64.10.0.28000.2526' to folder 'D:\a\mousepods\mousepods\windows\driver\packages'
Installed Microsoft.Windows.SDK.CPP 10.0.28000.2526 from https://api.nuget.org/v3/index.json to C:\Users\runneradmin\.nuget\packages\microsoft.windows.sdk.cpp\10.0.28000.2526 with content hash 3NgnLMm7bkOc/yrO9UP3YVGjpdIlA/cA7l5n+Z3SjX7HKwqCU4ClcAursh/4xdBR/UlFmamtv8or8gQxTOGN+A==.
Adding package 'Microsoft.Windows.SDK.CPP.10.0.28000.2526' to folder 'D:\a\mousepods\mousepods\windows\driver\packages'
Added package 'Microsoft.Windows.SDK.CPP.10.0.28000.2526' to folder 'D:\a\mousepods\mousepods\windows\driver\packages'
  GET https://api.nuget.org/v3/vulnerabilities/index.json
  OK https://api.nuget.org/v3/vulnerabilities/index.json 2ms
  GET https://api.nuget.org/v3-vulnerabilities/2026.11.07.05.53.18/vulnerability.base.json
  GET https://api.nuget.org/v3-vulnerabilities/2026.11.07.05.53.18/2026.11.09.11.53.27/vulnerability.update.json
  OK https://api.nuget.org/v3-vulnerabilities/2026.11.07.05.53.18/2026.11.09.11.53.27/vulnerability.update.json 2ms
  OK https://api.nuget.org/v3-vulnerabilities/2026.11.07.05.53.18/vulnerability.base.json 4ms

NuGet Config files used:
    C:\Users\runneradmin\AppData\Roaming\NuGet\NuGet.Config
    C:\Program Files (x86)\NuGet\Config\Microsoft.VisualStudio.FallbackLocation.config
    C:\Program Files (x86)\NuGet\Config\Microsoft.VisualStudio.Offline.config

Feeds used:
    https://api.nuget.org/v3/index.json
    C:\Program Files (x86)\Microsoft SDKs\NuGetPackages\

Installed:
    3 package(s) to packages.config projects
```

## msbuild output (last 150 lines)

```

  Building 'MousePodsL2cap' with toolset 'WindowsKernelModeDriver10.0' and the 'Windows Driver' target platform.
  Stamping x64\Release\MousePodsL2cap.inf
  Stamping [Version] section with DriverVer=11/10/2026,1.18.13.674
  driver.c
  device.c
  queue.c
  l2cap.c
  log.c
  Running Code Analysis for C/C++...
  Generating Code...
  MousePodsL2cap.vcxproj -> D:\a\mousepods\mousepods\windows\driver\x64\Release\MousePodsL2cap.sys
  Driver is 'Windows Driver'.
  ........................
  Signability test complete.
  
  Errors:
  None
  
  Warnings:
  None
  
  Catalog generation complete.
  D:\a\mousepods\mousepods\windows\driver\x64\Release\MousePodsL2cap\mousepodsl2cap.cat
  Set catalog attribute.
  WdkVersion: 10.0.28000.0
  Set catalog attribute.
  WdkAssemblyVersion: 10.0.28000.2526
```

Commit 4aebcde97e34555fef69c7fc8dfbc508bc0fe9c3, build job success, https://github.com/henningnorden/mousepods/actions/runs/42734630260
