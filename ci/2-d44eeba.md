# Windows driver build: run 2, commit d44eeba

Status: **failure**

Compiler warnings: 0. /analyze findings: 0. InfVerif: not clean.

## Failing lines

```
MSBuild 17 (Visual Studio 2022) not found; installed: C:\Program Files\Microsoft Visual Studio\18\Enterprise
```

## Steps

| Step | Result | Detail |
|---|---|---|
| nuget restore | ok | exit code 0 |
| msbuild | FAILED | not run: MSBuild 17 not found |
| package | FAILED | not run: no MousePodsL2cap.sys |
| InfVerif /w | FAILED | not run: no package |
| signing | FAILED | not run: no package |
| driver.zip | FAILED | not run: package not complete |

## /analyze findings (0)

None.

## Compiler warnings (0)

None.

## nuget restore output

```
Restoring NuGet package Microsoft.Windows.WDK.x64.10.0.26100.6584.
Restoring NuGet package Microsoft.Windows.SDK.CPP.10.0.26100.6584.
Restoring NuGet package Microsoft.Windows.SDK.CPP.x64.10.0.26100.6584.
  GET https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.cpp/10.0.26100.6584/microsoft.windows.sdk.cpp.10.0.26100.6584.nupkg
  GET https://api.nuget.org/v3-flatcontainer/microsoft.windows.wdk.x64/10.0.26100.6584/microsoft.windows.wdk.x64.10.0.26100.6584.nupkg
  GET https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.cpp.x64/10.0.26100.6584/microsoft.windows.sdk.cpp.x64.10.0.26100.6584.nupkg
  OK https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.cpp/10.0.26100.6584/microsoft.windows.sdk.cpp.10.0.26100.6584.nupkg 8ms
  OK https://api.nuget.org/v3-flatcontainer/microsoft.windows.wdk.x64/10.0.26100.6584/microsoft.windows.wdk.x64.10.0.26100.6584.nupkg 14ms
  OK https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.cpp.x64/10.0.26100.6584/microsoft.windows.sdk.cpp.x64.10.0.26100.6584.nupkg 19ms
Installed Microsoft.Windows.SDK.CPP.x64 10.0.26100.6584 from https://api.nuget.org/v3/index.json to C:\Users\runneradmin\.nuget\packages\microsoft.windows.sdk.cpp.x64\10.0.26100.6584 with content hash fIfrF5Y969hw9BkJpWyJsM//ZDc3H7MgAu5SgZaaGW7TiG9kKVTwjFXpbt9fysW4VzP8xhORT1ZXBOvkAyZuYw==.
Adding package 'Microsoft.Windows.SDK.CPP.x64.10.0.26100.6584' to folder 'D:\a\mousepods\mousepods\windows\driver\packages'
Added package 'Microsoft.Windows.SDK.CPP.x64.10.0.26100.6584' to folder 'D:\a\mousepods\mousepods\windows\driver\packages'
Installed Microsoft.Windows.WDK.x64 10.0.26100.6584 from https://api.nuget.org/v3/index.json to C:\Users\runneradmin\.nuget\packages\microsoft.windows.wdk.x64\10.0.26100.6584 with content hash 4fISvN7sp2jO6r4v+KNkTguBZqt0Nxo3AJhVdyIvhUrPfG2ML8BecRIuo+SRgIGbqhklgsE7mLH7Vx2kjW2cmA==.
Adding package 'Microsoft.Windows.WDK.x64.10.0.26100.6584' to folder 'D:\a\mousepods\mousepods\windows\driver\packages'
Added package 'Microsoft.Windows.WDK.x64.10.0.26100.6584' to folder 'D:\a\mousepods\mousepods\windows\driver\packages'
Installed Microsoft.Windows.SDK.CPP 10.0.26100.6584 from https://api.nuget.org/v3/index.json to C:\Users\runneradmin\.nuget\packages\microsoft.windows.sdk.cpp\10.0.26100.6584 with content hash ohAJqoY7QLSvpfMLOdGa3AyHWaVpXeXuS241ULB/fsVcDIiSTbKreLBzm4uRth3uhp9fjOV4lI8sK28sWM7QTg==.
Adding package 'Microsoft.Windows.SDK.CPP.10.0.26100.6584' to folder 'D:\a\mousepods\mousepods\windows\driver\packages'
Added package 'Microsoft.Windows.SDK.CPP.10.0.26100.6584' to folder 'D:\a\mousepods\mousepods\windows\driver\packages'
  GET https://api.nuget.org/v3/vulnerabilities/index.json
  OK https://api.nuget.org/v3/vulnerabilities/index.json 5ms
  GET https://api.nuget.org/v3-vulnerabilities/2026.11.07.05.53.18/vulnerability.base.json
  GET https://api.nuget.org/v3-vulnerabilities/2026.11.07.05.53.18/2026.11.09.11.53.27/vulnerability.update.json
  OK https://api.nuget.org/v3-vulnerabilities/2026.11.07.05.53.18/2026.11.09.11.53.27/vulnerability.update.json 3ms
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

Commit d44eeba5f747ef99572d75d1c82b723e2f886675, build job failure, https://github.com/henningnorden/mousepods/actions/runs/42733027265
