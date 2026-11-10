# SPDX-License-Identifier: Apache-2.0
<#
One VM test round for the MousePods L2CAP driver: every variant in one run.

    powershell -ExecutionPolicy Bypass -File windows\vm\round.ps1 [-Address AA:BB:CC:DD:EE:FF]

What it does, in this order: checks the VM setup (setup.txt), takes the
test-signed driver that GitHub Actions built for the checked-out commit from
the vm-results branch, installs it, runs the 16 variants and 4 checks of
run_variants.py one by one, and pushes the round folder (report.md,
results.json, driver.log, setup.txt, crash.txt after a bluescreen) to
vm-results with the VM deploy key.

A bluescreen only costs the variant that caused it: after the restart, log in
again and the round goes on by itself (a logon task runs round.ps1 -Resume).

    -Resume     continue the unfinished round (the logon task and the desktop file use this)
    -Push DIR   push a round folder that could not be pushed before
    -Uninstall  remove the driver, the root device, the certificates and the logon task
    -KeepDump   also keep bluescreen minidumps in %ProgramData%\MousePods\dumps (never pushed)
    -SelfTest   compile the C# helpers only (CI)

Everything pushed is masked first: Bluetooth addresses keep only their last two
bytes, and the Windows user and computer names are removed.
#>
[CmdletBinding()]
param(
    [string]$Address,
    [switch]$Resume,
    [string]$Push,
    [switch]$Uninstall,
    [switch]$SelfTest,
    [switch]$SkipUpdateCheck,
    [switch]$KeepDump,
    [switch]$FromTask
)

$ErrorActionPreference = 'Stop'

$VmDir = $PSScriptRoot
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$RunPy = Join-Path $VmDir 'run_variants.py'
$Data = Join-Path $env:ProgramData 'MousePods'
$ActivePath = Join-Path $Data 'round.json'
$MarkerPath = Join-Path $Data 'current.json'
$ResultsTree = Join-Path $Data 'vm-results'
$TaskName = 'MousePods VM round'
$PushUrl = 'git@github.com:henningnorden/mousepods.git'
$Branch = 'windows-l2cap-driver'
$KeyPath = Join-Path $env:USERPROFILE '.ssh\mousepods_vm'
$ServiceKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\MousePodsL2cap'
$StepTimeoutS = 180
$ToolTimeoutS = 180           # devnode.ps1, Bluetooth service switch, other helper calls
$InstallTimeoutS = 300        # pnputil /add-driver, /delete-driver
$CrashControlSaved = Join-Path $Data 'crashcontrol.json'
$script:LogFile = $null
$ResumeFile = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Continue MousePods round.cmd'

$helperSource = @'
using System;
using System.Runtime.InteropServices;

namespace MousePodsVm
{
    [StructLayout(LayoutKind.Sequential)]
    struct PropertyKey { public Guid fmtid; public int pid; }

    [StructLayout(LayoutKind.Sequential)]
    struct PropVariant { public ushort vt; public ushort r1, r2, r3; public IntPtr p; public IntPtr p2; }

    [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore
    {
        [PreserveSig] int GetCount(out int count);
        [PreserveSig] int GetAt(int index, out PropertyKey key);
        [PreserveSig] int GetValue(ref PropertyKey key, out PropVariant value);
    }

    [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice
    {
        [PreserveSig] int Activate(ref Guid iid, int context, IntPtr parameters, out IntPtr result);
        [PreserveSig] int OpenPropertyStore(int access, out IPropertyStore store);
        [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
    }

    [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator
    {
        [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr devices);
        [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
    }

    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    class MMDeviceEnumeratorCom { }

    public static class Helpers
    {
        // Friendly name of the default playback device (eRender, eConsole).
        public static string DefaultOutput()
        {
            IMMDeviceEnumerator e = (IMMDeviceEnumerator)new MMDeviceEnumeratorCom();
            IMMDevice d;
            int hr = e.GetDefaultAudioEndpoint(0, 0, out d);
            if (hr != 0) return "none (0x" + hr.ToString("X8") + ")";
            IPropertyStore s;
            hr = d.OpenPropertyStore(0, out s);
            if (hr != 0) return "unknown (0x" + hr.ToString("X8") + ")";
            PropertyKey k = new PropertyKey();
            k.fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0");   // PKEY_Device_FriendlyName
            k.pid = 14;
            PropVariant v;
            hr = s.GetValue(ref k, out v);
            if (hr != 0 || v.vt != 31 || v.p == IntPtr.Zero) return "unknown";
            return Marshal.PtrToStringUni(v.p);
        }

        [DllImport("kernel32.dll")]
        static extern uint SetThreadExecutionState(uint flags);

        // Keep Windows from sleeping while the round runs (ES_CONTINUOUS | ES_SYSTEM_REQUIRED).
        public static void KeepAwake() { SetThreadExecutionState(0x80000001u); }
    }
}
'@

function Add-Helpers {
    if (-not ('MousePodsVm.Helpers' -as [type])) {
        Add-Type -TypeDefinition $helperSource -Language CSharp
    }
}

# --- output -------------------------------------------------------------------

# Everything shown also goes to round.log (masked before it is pushed).
function Say([string]$Text = '', [string]$Color = 'Gray') {
    Write-Host $Text -ForegroundColor $Color
    if ($script:LogFile) {
        try { Add-Content -Path $script:LogFile -Value "$(Get-Date -Format 'HH:mm:ss') $Text" -Encoding UTF8 } catch { $null = $_ }
    }
}

function Banner([string[]]$Lines, [string]$Color = 'Yellow') {
    Say ''
    Say ('#' * 72) $Color
    foreach ($l in $Lines) { Say "#  $l" $Color }
    Say ('#' * 72) $Color
    Say ''
}

function Wait-Enter([int]$Seconds) {
    $end = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $end) {
        try {
            if ([Console]::KeyAvailable -and [Console]::ReadKey($true).Key -eq 'Enter') { return }
        } catch { }
        Start-Sleep -Milliseconds 200
    }
}

function Close-Window {
    Say ''
    Read-Host 'Press Enter to close this window' | Out-Null
}

# Writes text and forces it to disk, so a bluescreen right after keeps it.
function Write-Durable([string]$Path, [string]$Text) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
    $fs = New-Object IO.FileStream($Path, [IO.FileMode]::Create, [IO.FileAccess]::Write,
        [IO.FileShare]::Read, 4096, [IO.FileOptions]::WriteThrough)
    try { $fs.Write($bytes, 0, $bytes.Length); $fs.Flush($true) } finally { $fs.Dispose() }
}

function Add-Line([string]$Path, [string]$Text) {
    Add-Content -Path $Path -Value $Text -Encoding UTF8
}

# --- native commands ----------------------------------------------------------

# Runs a program; its stderr never stops the script (Windows PowerShell turns
# stderr lines into errors). Returns exit code and all output lines.
function Invoke-Native([string]$Exe, [string[]]$ArgList) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = @(& $Exe @ArgList 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } catch {
        $out = @("$_")
        $code = -1
    } finally {
        $ErrorActionPreference = $old
    }
    [pscustomobject]@{ Code = $code; Out = $out; Text = ($out -join "`n") }
}

function Invoke-Git([string[]]$ArgList, [string]$Dir = $Repo) {
    Invoke-Native 'git' (@('-c', 'safe.directory=*', '-C', $Dir) + $ArgList)
}

# Windows command-line quoting for one argument (CommandLineToArgvW rules).
function Quote-Arg([string]$Arg) {
    if ($Arg -ne '' -and $Arg -notmatch '[\s"]') { return $Arg }
    $q = $Arg -replace '(\\*)"', '$1$1\"'
    $q = $q -replace '(\\+)$', '$1$1'
    return '"' + $q + '"'
}

# Like Invoke-Native, but in its own process with a time limit: driver-touching
# helpers can block in the kernel, and the round must go on (or stop with a
# report) instead of hanging. Code is 'timeout' when the limit was hit.
function Invoke-Timed([string]$Exe, [string[]]$ArgList, [int]$TimeoutS = $ToolTimeoutS) {
    $id = [guid]::NewGuid().ToString('N')
    $o = Join-Path $env:TEMP "mousepods-$id.out"
    $e = Join-Path $env:TEMP "mousepods-$id.err"
    $line = ($ArgList | ForEach-Object { Quote-Arg $_ }) -join ' '
    try {
        $p = Start-Process -FilePath $Exe -ArgumentList $line -NoNewWindow -PassThru `
            -RedirectStandardOutput $o -RedirectStandardError $e
    } catch {
        return [pscustomobject]@{ Code = -1; Out = @("$_"); Text = "$_"; TimedOut = $false }
    }
    $null = $p.Handle
    $timedOut = -not $p.WaitForExit($TimeoutS * 1000)
    if ($timedOut) {
        $null = Invoke-Native 'taskkill.exe' @('/PID', "$($p.Id)", '/T', '/F')
        $code = 'timeout'
    } else {
        $p.WaitForExit()
        $code = $p.ExitCode
    }
    $out = @()
    foreach ($f in @($o, $e)) {
        if (Test-Path $f) {
            $out += @(Get-Content -Path $f -Encoding UTF8 -ErrorAction SilentlyContinue)
            Remove-Item $f -Force -ErrorAction SilentlyContinue
        }
    }
    if ($timedOut) { $out += "(did not finish in $TimeoutS s and was stopped)" }
    [pscustomobject]@{ Code = $code; Out = $out; Text = ($out -join "`n"); TimedOut = $timedOut }
}

function Find-Python {
    $candidates = @()
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { $candidates += , @($py.Source, '-3') }
    foreach ($c in @(Get-Command python.exe -All -ErrorAction SilentlyContinue)) {
        if ($c.Source -notlike '*\WindowsApps\*') { $candidates += , @($c.Source) }
    }
    foreach ($c in $candidates) {
        $exe = $c[0]
        $pre = @()
        if ($c.Count -gt 1) { $pre = $c[1..($c.Count - 1)] }
        $r = Invoke-Native $exe ($pre + @('-c', 'import sys; print(sys.executable); print(sys.version_info >= (3, 10))'))
        if ($r.Code -eq 0 -and $r.Out.Count -ge 2 -and $r.Out[1] -eq 'True') { return $r.Out[0].Trim() }
    }
    return $null
}

function Invoke-Py([string[]]$ArgList, [int]$TimeoutS = $ToolTimeoutS) {
    if (-not $script:Python) { return [pscustomobject]@{ Code = -1; Out = @('no Python'); Text = 'no Python'; TimedOut = $false } }
    $env:PYTHONIOENCODING = 'utf-8'
    Invoke-Timed $script:Python (@('-u', $RunPy) + $ArgList) $TimeoutS
}

function Last-Json($Result) {
    $line = @($Result.Out | Where-Object { $_.Trim().StartsWith('{') -or $_.Trim().StartsWith('[') })
    if (-not $line) { return $null }
    try { return ($line[-1] | ConvertFrom-Json) } catch { return $null }
}

function Quote([string]$s) { '"' + $s + '"' }

# AA:BB:CC:DD:3F:A1 -> XX:XX:XX:XX:3F:A1 (what may appear in pushed files)
function Get-MaskedAddr([string]$Addr) {
    if (-not $Addr) { return $Addr }
    $p = $Addr -split '[:-]'
    if ($p.Count -ne 6) { return $Addr }
    "XX:XX:XX:XX:$($p[4]):$($p[5])"
}

function Use-DeployKey {
    $k = $KeyPath -replace '\\', '/'
    $env:GIT_SSH_COMMAND = "ssh -i `"$k`" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=20"
    $env:GIT_AUTHOR_NAME = 'MousePods VM'
    $env:GIT_AUTHOR_EMAIL = 'heno@henomedia.de'
    $env:GIT_COMMITTER_NAME = 'MousePods VM'
    $env:GIT_COMMITTER_EMAIL = 'heno@henomedia.de'
}

# --- the vm-results worktree --------------------------------------------------

# Fetches vm-results into the main clone and returns the commit (FETCH_HEAD is
# per worktree, so callers use this commit, never FETCH_HEAD in the results tree).
function Fetch-Results([string]$From) {
    $f = Invoke-Git @('fetch', '-q', $From, 'vm-results')
    if ($f.Code -ne 0) { return [pscustomobject]@{ Sha = $null; Text = $f.Text } }
    $sha = (Invoke-Git @('rev-parse', 'FETCH_HEAD')).Out[0].Trim()
    [pscustomobject]@{ Sha = $sha; Text = '' }
}

# Checks out that vm-results commit into $ResultsTree, a worktree of the clone.
function Reset-ResultsTree([string]$Sha) {
    if (Test-Path (Join-Path $ResultsTree '.git')) {
        $r = Invoke-Git @('checkout', '-q', '-f', '--detach', $Sha) $ResultsTree
        if ($r.Code -eq 0) {
            $null = Invoke-Git @('clean', '-fdxq') $ResultsTree
            return $true
        }
    }
    if (Test-Path $ResultsTree) { Remove-Item -Recurse -Force $ResultsTree }
    $null = Invoke-Git @('worktree', 'prune')
    $r = Invoke-Git @('worktree', 'add', '--detach', $ResultsTree, $Sha)
    return ($r.Code -eq 0)
}

# Returns 'pushed', 'failed' (network, key, git: can be pushed later with -Push) or
# 'masking' (an address could not be masked: never pushed, not retried).
function Push-RoundFolder([string]$Dir, [string]$Message) {
    $stamp = Split-Path $Dir -Leaf
    # one line, no double quotes (Windows PowerShell passes them to git unescaped)
    $Message = ($Message -replace '"', "'" -replace '[\r\n]+', ' ')
    if ($Message.Length -gt 200) { $Message = $Message.Substring(0, 197) + '...' }
    if (-not (Test-Path $KeyPath)) {
        Say "No deploy key at $KeyPath, so the round cannot be pushed." Red
        return 'failed'
    }
    Use-DeployKey
    $last = ''
    $committed = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            if ($attempt -gt 1) { Start-Sleep -Seconds (5 * $attempt) }
            $f = Fetch-Results $PushUrl
            if (-not $f.Sha) { $last = $f.Text; continue }
            if (-not $committed) {
                if (-not (Reset-ResultsTree $f.Sha)) { $last = "could not check out vm-results into $ResultsTree"; continue }
                $dest = Join-Path $ResultsTree "rounds\$stamp"
                New-Item -ItemType Directory -Force -Path $dest | Out-Null
                Copy-Item -Path (Join-Path $Dir '*') -Destination $dest -Recurse -Force
                Get-ChildItem $dest -Filter '*.dmp' -Recurse | Remove-Item -Force    # never pushed
                $ma = @('mask', '--dir', $dest, '--state', $Dir, '--hide', $env:USERNAME, $env:COMPUTERNAME)
                if ($script:Addr) { $ma += @('--addr', $script:Addr) }
                $m = Invoke-Py $ma
                if ($m.Code -ne 0) {
                    Remove-Item -Recurse -Force $dest
                    $null = Reset-ResultsTree $f.Sha
                    Say "Not pushing: an address could not be masked. $($m.Text)" Red
                    return 'masking'
                }
                $null = Invoke-Git @('add', "rounds/$stamp") $ResultsTree
                $c = Invoke-Git @('commit', '-q', '-m', $Message) $ResultsTree
                if ($c.Code -ne 0) { $last = $c.Text; continue }
                $committed = $true
            } else {
                # pull --rebase: put the round commit on top of what arrived meanwhile
                $rb = Invoke-Git @('rebase', $f.Sha) $ResultsTree
                if ($rb.Code -ne 0) {
                    $null = Invoke-Git @('rebase', '--abort') $ResultsTree
                    $last = $rb.Text
                    continue
                }
            }
            $p = Invoke-Git @('push', $PushUrl, 'HEAD:vm-results') $ResultsTree
            if ($p.Code -eq 0) {
                Say "Pushed to vm-results: rounds/$stamp" Green
                return 'pushed'
            }
            $last = $p.Text
            Say "Push attempt $attempt failed, trying again." Yellow
        } catch {
            $last = "$($_.Exception.Message)"
        }
    }
    Say "Pushing failed: $last" Red
    return 'failed'
}

# --- crash analysis -----------------------------------------------------------

function Find-Cdb {
    $paths = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\Debuggers\x64\cdb.exe'),
        (Join-Path $env:ProgramFiles 'Windows Kits\10\Debuggers\x64\cdb.exe')
    )
    foreach ($p in $paths) { if (Test-Path $p) { return $p } }
    $c = Get-Command cdb.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    return $null
}

# What happened after the marker was written: a minidump, a bugcheck event, or just a reset.
function Get-CrashInfo($Marker, [string]$RoundDir, [string]$PkgDir) {
    $since = [DateTime]::new([long]$Marker.started_ticks, [DateTimeKind]::Utc)
    $crashTxt = Join-Path $RoundDir 'crash.txt'
    $info = @{ kind = 'interrupted'; dump = $null; at_boot = [bool]$env:SAFEBOOT_OPTION }
    Add-Line $crashTxt "=== step $($Marker.id) $($Marker.name), phase $($Marker.phase), started $($Marker.started) UTC"
    $dumps = @(Get-ChildItem -Path "$env:SystemRoot\Minidump\*.dmp" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTimeUtc -gt $since } | Sort-Object LastWriteTimeUtc -Descending)
    $full = Get-Item "$env:SystemRoot\MEMORY.DMP" -ErrorAction SilentlyContinue
    $dump = $null
    if ($dumps) { $dump = $dumps[0] }
    elseif ($full -and $full.LastWriteTimeUtc -gt $since) { $dump = $full }
    $events = @()
    try {
        $events = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = 1001; StartTime = $since.ToLocalTime() } -ErrorAction SilentlyContinue |
            Where-Object { $_.ProviderName -like '*WER-SystemErrorReporting*' -or $_.Message -match 'bug ?check' })
    } catch { }
    if ($dump) {
        $info.kind = 'bugcheck'
        $info.dump = $dump.Name
        Add-Line $crashTxt "dump: $($dump.FullName), $($dump.Length) bytes, $($dump.LastWriteTime)"
        if ($script:KeepDumps -and $dump.Length -lt 64MB) {
            # local only: dumps hold memory contents, and vm-results is public
            $keep = Join-Path $Data 'dumps'
            New-Item -ItemType Directory -Force -Path $keep | Out-Null
            Copy-Item $dump.FullName (Join-Path $keep "$(Split-Path $RoundDir -Leaf)-step$($Marker.id)-$($dump.Name)")
        }
        $cdb = Find-Cdb
        if ($cdb) {
            $sym = "srv*$Data\symbols*https://msdl.microsoft.com/download/symbols"
            if ($PkgDir) { $sym = "$PkgDir;$sym" }
            $outFile = Join-Path $env:TEMP "mousepods-cdb-$($Marker.id).txt"
            $argLine = "-z $(Quote $dump.FullName) -y $(Quote $sym) -c $(Quote '!analyze -v; lmvm MousePodsL2cap; q')"
            Say "Analyzing the crash dump with cdb (can take a few minutes)..." Cyan
            $p = Start-Process -FilePath $cdb -ArgumentList $argLine -NoNewWindow -PassThru -RedirectStandardOutput $outFile
            $null = $p.Handle
            if (-not $p.WaitForExit(600000)) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
            if (Test-Path $outFile) { Add-Line $crashTxt (Get-Content $outFile -Raw) }
        } else {
            Add-Line $crashTxt 'cdb not found (Debugging Tools for Windows not installed); only the dump file name is recorded.'
        }
    } elseif ($events) {
        $info.kind = 'bugcheck'
        Add-Line $crashTxt "no dump file; bugcheck event: $($events[0].Message)"
    } else {
        Add-Line $crashTxt 'no dump file and no bugcheck event: Windows was reset or switched off during this step.'
    }
    if ($info.at_boot) { Add-Line $crashTxt "Windows started in Safe Mode ($env:SAFEBOOT_OPTION): the driver crashed again during startup." }
    return $info
}

# --- driver install and routes ------------------------------------------------

function Invoke-Devnode([string]$Action, [string]$Inf, [string]$Log) {
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $VmDir 'devnode.ps1'), '-Action', $Action)
    if ($Inf) { $a += @('-InfPath', $Inf) }
    $r = Invoke-Timed 'powershell.exe' $a $ToolTimeoutS
    if ($Log) { Add-Line $Log "devnode $Action -> exit $($r.Code)`n$($r.Text)" }
    $r
}

function Set-BtService([string]$State, [string]$Log) {
    $r = Invoke-Py @('service', '--addr', $script:Addr, '--state', $State)
    if ($Log) { Add-Line $Log "Bluetooth service $State -> $($r.Text)" }
    $r
}

function Set-DriverStart([int]$Value) {
    if (Test-Path $ServiceKey) { Set-ItemProperty -Path $ServiceKey -Name Start -Value $Value }
}

# Returns the text of what hung, or $null.
function Remove-OldDrivers([string]$Log) {
    $hung = @()
    $r = Invoke-Devnode 'remove' $null $Log
    if ($r.TimedOut) { $hung += 'removing the root device' }
    if ($script:Addr) {
        $r = Set-BtService 'disable' $Log
        if ($r.TimedOut) { $hung += 'switching the Bluetooth service off' }
    }
    $infs = @(Select-String -Path "$env:SystemRoot\INF\oem*.inf" -Pattern 'MousePodsL2cap.sys' -SimpleMatch -List -ErrorAction SilentlyContinue)
    foreach ($i in $infs) {
        $r = Invoke-Timed 'pnputil.exe' @('/delete-driver', $i.Filename, '/uninstall', '/force') $InstallTimeoutS
        Add-Line $Log "pnputil /delete-driver $($i.Filename) -> exit $($r.Code)`n$($r.Text)"
        if ($r.TimedOut) { $hung += "pnputil /delete-driver $($i.Filename)" }
    }
    if ($hung) { return ($hung -join ', ') }
    return $null
}

# Nothing of either route stays: no root device, service off on the AirPods.
# Returns the text of what hung, or $null.
function Clear-Routes([string]$Log) {
    $hung = @()
    $r = Invoke-Devnode 'remove' $null $Log
    if ($r.TimedOut) { $hung += 'removing the root device' }
    if ($script:Addr) {
        $r = Set-BtService 'disable' $Log
        if ($r.TimedOut) { $hung += 'switching the Bluetooth service off' }
    }
    if ($hung) { return ($hung -join ', ') }
    return $null
}

# Clean switch: nothing of the other route stays, then the wanted one is set up.
# Returns $null, or what hung and where: 'teardown' (taking the previous route
# down: the device is stuck, no route is to blame, Windows needs a restart) or
# 'setup' (creating or enabling the wanted route: that route is skipped).
function Set-Route([string]$Route, [string]$Inf, [string]$Log) {
    Say "Setting up load route $Route ..." Cyan
    Add-Line $Log "=== route $Route, $(Get-Date -Format o)"
    $hung = Clear-Routes $Log
    if ($hung) { return [pscustomobject]@{ Where = 'teardown'; What = $hung } }
    Set-DriverStart 3
    Start-Sleep -Seconds 2
    if ($Route -eq 'root') {
        $r = Invoke-Devnode 'create' $Inf $Log
        if ($r.TimedOut) { return [pscustomobject]@{ Where = 'setup'; What = 'creating the root device' } }
    } else {
        $r = Set-BtService 'enable' $Log
        if ($r.TimedOut) { return [pscustomobject]@{ Where = 'setup'; What = 'switching the Bluetooth service on' } }
    }
    return $null
}

# A device could not be taken down: only a restart frees it. The round is not
# finished; after the restart and logon it goes on with the step that was next.
function Restart-ForStuckDevice($Round, [string]$What) {
    $n = 0
    if ($Round.PSObject.Properties['teardown_restarts']) { $n = [int]$Round.teardown_restarts }
    if ($n -ge 2) {
        Stop-Round $Round "Taking the driver's device down hung again after $n restarts ($What)."
    }
    if ($Round.PSObject.Properties['teardown_restarts']) { $Round.teardown_restarts = $n + 1 }
    else { $Round | Add-Member -NotePropertyName teardown_restarts -NotePropertyValue ($n + 1) }
    Save-Round $Round
    if (Test-Path $MarkerPath) {
        # a crash found at resume is still recorded; a route marker from the step loop is not (no route to blame)
        $mk = Get-Content $MarkerPath -Raw | ConvertFrom-Json
        if ($mk.PSObject.Properties['crash']) { $null = Invoke-Py @('next', '--dir', $Round.dir, '--marker', $MarkerPath) }
    }
    Remove-Item $MarkerPath -Force -ErrorAction SilentlyContinue
    Add-Line (Join-Path $Round.dir 'install.txt') "teardown hung ($What); restart $($n + 1)"
    Banner @("Windows could not take the driver's device down ($What).",
        'Only a restart frees it. Windows restarts in 60 seconds (press Enter to restart now);',
        'log in again afterwards and the round continues by itself.',
        'If Windows has not restarted after 10 minutes, use Force Reset in virt-manager.') Yellow
    Wait-Enter 60
    Restart-Computer -Force
    exit 0
}

# --- setup checks ---------------------------------------------------------------

function Get-TestSigning {
    $opts = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control' -ErrorAction SilentlyContinue).SystemStartOptions
    $active = "$opts" -match 'TESTSIGNING'
    $bcd = Invoke-Native 'bcdedit.exe' @('/enum', '{current}')
    $set = $bcd.Text -match '(?im)^testsigning\s+(Yes|Ja|On)'
    [pscustomobject]@{ Active = $active; Set = $set; Bcd = $bcd.Text }
}

function Get-SetupFacts {
    $facts = [ordered]@{}
    $cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $facts['Windows'] = "$($cv.ProductName) $($cv.DisplayVersion), build $($cv.CurrentBuild).$($cv.UBR) (build 22000 and up is Windows 11)"
    $ts = Get-TestSigning
    $facts['Test signing'] = "active in this boot: $($ts.Active); set in bcdedit: $($ts.Set)"
    try { $facts['Secure Boot'] = "$(Confirm-SecureBootUEFI)" } catch { $facts['Secure Boot'] = "not available ($($_.Exception.Message))" }
    try {
        $dg = Get-CimInstance -Namespace 'root\Microsoft\Windows\DeviceGuard' -ClassName Win32_DeviceGuard
        $facts['HVCI (memory integrity)'] = "running: $(@($dg.SecurityServicesRunning) -contains 2); configured: $(@($dg.SecurityServicesConfigured) -contains 2)"
    } catch { $facts['HVCI (memory integrity)'] = "unknown ($($_.Exception.Message))" }
    $facts['Python'] = (Invoke-Native $script:Python @('--version')).Text
    $facts['Git'] = (Invoke-Native 'git' @('--version')).Text
    $cc = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' -ErrorAction SilentlyContinue
    $facts['Crash dumps'] = "CrashDumpEnabled $($cc.CrashDumpEnabled), AutoReboot $($cc.AutoReboot), cdb: $(if (Find-Cdb) { Find-Cdb } else { 'not installed' })"
    try { Add-Helpers; $facts['Default audio output'] = [MousePodsVm.Helpers]::DefaultOutput() } catch { $facts['Default audio output'] = "unknown ($($_.Exception.Message))" }
    $facts
}

# setup.txt is written by run_variants.py, which masks the addresses in it.
function Write-Setup([string]$Path, $Facts, $Devices) {
    $list = New-Object System.Collections.ArrayList
    # [name, value] pairs; run_variants.py also reads {"k","v"} and 5.1's {"value","Count"} wrappers
    foreach ($k in $Facts.Keys) { $null = $list.Add([string[]]@("$k", "$($Facts[$k])")) }
    $devs = New-Object System.Collections.ArrayList
    foreach ($d in @($Devices)) {
        if ($d) { $null = $devs.Add([ordered]@{ name = "$($d.name)"; address = "$($d.address)"; connected = [bool]$d.connected }) }
    }
    $json = Join-Path $env:TEMP "mousepods-setup-$([guid]::NewGuid().ToString('N')).json"
    Set-Content -Path $json -Encoding UTF8 -Value (ConvertTo-Json -InputObject ([ordered]@{ facts = $list; devices = $devs }) -Depth 5)
    $r = Invoke-Py @('setup', '--path', $Path, '--facts', $json)
    Remove-Item $json -Force -ErrorAction SilentlyContinue
    if ($r.Code -ne 0) { Say "Writing setup.txt failed: $($r.Text)" Yellow }
}

function Register-ResumeTask {
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    # no trigger delay: -FromTask switches the driver off first, then waits itself
    $argLine = "-NoProfile -ExecutionPolicy Bypass -File $(Quote $PSCommandPath) -Resume -FromTask"
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argLine
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
    $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3)
    $null = Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force
    Set-Content -Path $ResumeFile -Encoding ASCII -Value @(
        '@echo off',
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Resume")
}

function Unregister-ResumeTask {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item $ResumeFile -Force -ErrorAction SilentlyContinue
}

function Save-Round($Round) {
    Write-Durable $ActivePath ($Round | ConvertTo-Json -Depth 5)
}

# Every ending goes through here: report, push, clean up, tell Henning.
function Finish-Round($Round, [string]$Fatal) {
    $dir = $Round.dir
    # Leave nothing that loads the driver at the next start: no root device, service off.
    if ($Round.phase -eq 'variants') {
        $hung = Clear-Routes (Join-Path $dir 'install.txt')
        if ($hung) { Say "Cleaning up did not finish ($hung)." Yellow }
        Set-DriverStart 3
    }
    $outcome = $null
    $why = 'Python not found'
    if ($script:Python) {
        $a = @('report', '--dir', $dir)
        if ($Fatal) { $a += @('--fatal', $Fatal) }
        $r = Invoke-Py $a
        $outcome = ($r.Out | Where-Object { $_ -like 'Outcome:*' } | Select-Object -Last 1)
        $why = $r.Text
    }
    if (-not $outcome) {
        if ($Fatal) { $outcome = "Outcome: the round stopped before the variants: $Fatal" }
        else { $outcome = 'Outcome: unknown, the report could not be written (run_variants.py report failed); see results.json' }
        Set-Content -Path (Join-Path $dir 'report.md') -Encoding UTF8 -Value @("# VM round $(Split-Path $dir -Leaf)", '', $outcome, '',
            'run_variants.py report said:', '', '```', $why, '```', '')
    }
    if ($Round.pkg) {
        $sys = Join-Path $env:SystemRoot 'System32\drivers\MousePodsL2cap.sys'
        $want = Join-Path $Round.pkg 'MousePodsL2cap.sys'
        if ((Test-Path $sys) -and (Test-Path $want)) {
            $same = (Get-FileHash $sys).Hash -eq (Get-FileHash $want).Hash
            Add-Line (Join-Path $dir 'setup.txt') "Loaded .sys is the one from the package: $same"
        }
    }
    $pushed = Push-RoundFolder $dir "VM round $(Split-Path $dir -Leaf): $($outcome -replace '^Outcome: ', '')"
    Unregister-ResumeTask
    Remove-Item $ActivePath, $MarkerPath -Force -ErrorAction SilentlyContinue
    $color = 'Green'
    if ($Fatal) { $color = 'Red' }
    $lines = @('The round is finished.', $outcome)
    if ($Fatal) { $lines = @('The round stopped:', $Fatal, '', 'Fix this and run round.ps1 again.') }
    if ($pushed -eq 'pushed') { $lines += @('', 'The report is on GitHub (vm-results branch). Nothing else to do.') }
    elseif ($pushed -eq 'masking') {
        $lines += @('', 'The report was NOT pushed: a Bluetooth address in it could not be masked,',
            'and the fork is public. The round folder is kept on this VM only:', $dir,
            'Tell Claude; do not push it by hand.')
    } else { $lines += @('', 'The report could NOT be pushed. It is kept here:', $dir,
            "Push it later with: round.ps1 -Push `"$dir`"") }
    Banner $lines $color
    Close-Window
    if ($Fatal) { exit 1 }
    exit 0
}

function Stop-Round($Round, [string]$Message) {
    Say $Message Red
    Finish-Round $Round $Message
}

# --- the step loop --------------------------------------------------------------

function Write-Marker($Step, [string]$Phase) {
    $now = (Get-Date).ToUniversalTime()
    $m = [ordered]@{ id = [int]$Step.id; name = $Step.name; route = $Step.route; phase = $Phase
        started = $now.ToString('yyyy-MM-ddTHH:mm:ssZ'); started_ticks = $now.Ticks }
    Write-Durable $MarkerPath ($m | ConvertTo-Json)
}

function Invoke-Step($Round, $Step) {
    $argLine = (@('-u', (Quote $RunPy), 'step', '--dir', (Quote $Round.dir), '--id', $Step.id, '--addr', $Round.addr) -join ' ')
    if ($Step.kind -eq 'check') {
        $argLine += " --route $($Step.route) --mtu $($Step.mtu) --security $($Step.security)"
    }
    $p = Start-Process -FilePath $script:Python -ArgumentList $argLine -NoNewWindow -PassThru
    $null = $p.Handle
    if (-not $p.WaitForExit($StepTimeoutS * 1000)) {
        $null = Invoke-Native 'taskkill.exe' @('/PID', "$($p.Id)", '/T', '/F')
        $null = Invoke-Py @('record', '--dir', $Round.dir, '--id', "$($Step.id)", '--verdict', 'hung',
            '--detail', "did not finish in $StepTimeoutS s and was stopped")
        Say "Step $($Step.id) did not finish in $StepTimeoutS s; stopped it and going on." Yellow
    }
}

function Wait-AirPodsConnected($Round) {
    for ($i = 0; $i -lt 30; $i++) {
        $devs = Last-Json (Invoke-Py @('devices'))
        $me = @($devs | Where-Object { $_.address -eq $Round.addr })
        if ($me -and $me[0].connected) { return $true }
        if ($i -eq 0) {
            Banner @('The AirPods are not connected to Windows.',
                'Open the case next to the PC and put the AirPods in your ears.',
                'Waiting up to 90 seconds...')
        }
        Start-Sleep -Seconds 3
    }
    Say 'The AirPods did not connect; going on anyway (the variants will show it).' Yellow
    return $false
}

function Run-Steps($Round) {
    $install = Join-Path $Round.dir 'install.txt'
    $tone = Join-Path $Data 'tone440.wav'
    if (-not (Test-Path $tone)) { $null = Invoke-Py @('tone', $tone) }
    Add-Helpers
    [MousePodsVm.Helpers]::KeepAwake()
    $null = Wait-AirPodsConnected $Round
    Banner @('Put the AirPods in your ears now.',
        'During the round (about 10 minutes) turn your head slowly left and right now and then.',
        'Some variants play a quiet beep tone on the AirPods; that is intended.',
        'Near the end you will be asked to put the AirPods into the case.',
        'Starting in 15 seconds (press Enter to start now).')
    Wait-Enter 15
    $current = $null
    while ($true) {
        $a = @('next', '--dir', $Round.dir)
        if (Test-Path $MarkerPath) { $a += @('--marker', $MarkerPath) }
        $r = Invoke-Py $a
        $step = Last-Json $r
        if (-not $step) { Stop-Round $Round "run_variants.py next failed: $($r.Text)" }
        Remove-Item $MarkerPath -Force -ErrorAction SilentlyContinue
        if ($step.done) {
            if ($step.stopped) { Banner @($step.stopped) Red }
            break
        }
        if ($step.route -ne $current) {
            Write-Marker $step 'route'
            $hung = Set-Route $step.route (Join-Path $Round.pkg 'MousePodsL2cap.inf') $install
            if ($hung -and $hung.Where -eq 'teardown') { Restart-ForStuckDevice $Round $hung.What }
            if ($hung) {
                Say "Setting up route $($step.route) hung ($($hung.What)); skipping that route." Yellow
                $rec = Invoke-Py @('record', '--dir', $Round.dir, '--id', "$($step.id)", '--verdict', 'hung',
                    '--route', $step.route, '--skip-route', '--detail', "setting up route $($step.route) hung: $($hung.What)")
                if ($rec.Code -ne 0) {
                    Stop-Round $Round "Setting up route $($step.route) hung ($($hung.What)) and recording that failed: $($rec.Text)"
                }
                Remove-Item $MarkerPath -Force -ErrorAction SilentlyContinue
                $current = $null
                continue
            }
            $current = $step.route
        }
        Write-Marker $step 'channel'
        $player = $null
        if ($step.audio -eq 'playing') {
            $player = New-Object System.Media.SoundPlayer $tone
            $player.PlayLooping()
            Start-Sleep -Seconds 2
        }
        Say ''
        Say "=== step $($step.id) of 20: $($step.name)" Cyan
        try { Invoke-Step $Round $step }
        finally { if ($player) { $player.Stop(); $player.Dispose() } }
        Remove-Item $MarkerPath -Force -ErrorAction SilentlyContinue
    }
    Finish-Round $Round $null
}

# A bluescreen (or reset) happened during the step in current.json.
function Resume-AfterCrash($Round) {
    if (-not (Test-Path $MarkerPath)) { return }
    $marker = Get-Content $MarkerPath -Raw | ConvertFrom-Json
    Banner @("Windows went down during step $($marker.id) ($($marker.name)).", 'Recording it and going on with the next step.')
    $crash = Get-CrashInfo $marker $Round.dir $Round.pkg
    $m = [ordered]@{ id = $marker.id; name = $marker.name; route = $marker.route; phase = $marker.phase; started = $marker.started
        started_ticks = $marker.started_ticks; crash = $crash }
    Write-Durable $MarkerPath ($m | ConvertTo-Json -Depth 4)
    if ($env:SAFEBOOT_OPTION) {
        # The driver crashes as soon as Windows loads it: keep it from loading, record, restart normally.
        Set-DriverStart 4
        $null = Invoke-Devnode 'remove' $null (Join-Path $Round.dir 'install.txt')
        $null = Invoke-Py @('next', '--dir', $Round.dir, '--marker', $MarkerPath)
        Remove-Item $MarkerPath -Force -ErrorAction SilentlyContinue
        Banner @('Windows is in Safe Mode because the driver crashed during startup.',
            'The driver is now switched off. Restart Windows normally:',
            'Start > Power > Restart. Log in, and the round goes on by itself.')
        Close-Window
        exit 0
    }
}

# --- entry points -----------------------------------------------------------------

if ($SelfTest) {
    Add-Helpers
    try { Say "default audio output: $([MousePodsVm.Helpers]::DefaultOutput())" } catch { Say "default audio output: $($_.Exception.Message)" }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $VmDir 'devnode.ps1') -Action compile
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    # setup.txt exactly as a round writes it: the JSON this PowerShell produces, read by run_variants.py
    $script:Python = Find-Python
    $facts = [ordered]@{ 'Windows' = 'selftest value'; 'Test signing' = 'active in this boot: True' }
    $devs = @([pscustomobject]@{ name = 'AirPods Pro'; address = 'A4:C3:F0:12:3F:A1'; connected = $true })
    $out = Join-Path $env:TEMP 'mousepods-selftest-setup.txt'
    Write-Setup $out $facts $devs
    $text = Get-Content $out -Raw -Encoding UTF8
    Say $text
    if ($text -notmatch 'Windows: selftest value' -or $text -notmatch 'Test signing: active in this boot: True' -or
        $text -match 'A4:C3:F0:12' -or $text -notmatch 'XX:XX:XX:XX:3F:A1') {
        Write-Host '::error file=windows/vm/round.ps1,title=Write-Setup selftest::setup.txt does not hold the facts or shows a full address'
        exit 1
    }
    $q = Quote-Arg 'C:\Program Files\x "y"\'
    if ($q -ne '"C:\Program Files\x \"y\"\\"') {
        Write-Host "::error file=windows/vm/round.ps1,title=Quote-Arg selftest::got $q"
        exit 1
    }
    Say 'round.ps1 selftest: ok'
    exit 0
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    $a = "-NoProfile -ExecutionPolicy Bypass -File $(Quote $PSCommandPath)"
    if ($Address) { $a += " -Address $Address" }
    if ($Resume) { $a += ' -Resume' }
    if ($Push) { $a += " -Push $(Quote $Push)" }
    if ($Uninstall) { $a += ' -Uninstall' }
    if ($SkipUpdateCheck) { $a += ' -SkipUpdateCheck' }
    if ($KeepDump) { $a += ' -KeepDump' }
    if ($FromTask) { $a += ' -FromTask' }
    Say 'round.ps1 needs administrator rights; Windows will ask for them now.' Yellow
    Start-Process powershell.exe -Verb RunAs -ArgumentList $a
    exit 0
}

# -Resume, first of all (before Bluetooth reconnects the AirPods and loads the
# driver through the service, route b): keep Windows from loading the driver.
# Set-Route switches it back on for the next step.
if ($Resume -or ((Test-Path $ActivePath) -and -not $Push -and -not $Uninstall)) { Set-DriverStart 4 }

New-Item -ItemType Directory -Force -Path $Data | Out-Null
$script:Python = Find-Python

if ($Push) {
    $ok = Push-RoundFolder $Push "VM round $(Split-Path $Push -Leaf), pushed later"
    Close-Window
    if ($ok -eq 'pushed') { exit 0 } else { exit 1 }
}

if ($Uninstall) {
    $log = Join-Path $Data 'uninstall.txt'
    $saved = $null
    if (Test-Path $ActivePath) { $saved = Get-Content $ActivePath -Raw | ConvertFrom-Json; $script:Addr = $saved.addr }
    if ($script:Python -and -not $script:Addr) {
        $pick = Last-Json (Invoke-Py @('devices')) | Where-Object { $_.name -like '*AirPods*' } | Select-Object -First 1
        if ($pick) { $script:Addr = $pick.address }
    }
    $null = Remove-OldDrivers $log
    Set-DriverStart 3       # in case a driver key is left behind
    if (Test-Path $CrashControlSaved) {
        $old = Get-Content $CrashControlSaved -Raw | ConvertFrom-Json
        $ccKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl'
        foreach ($n in @('CrashDumpEnabled', 'AutoReboot')) {
            if ($null -ne $old.$n) { Set-ItemProperty $ccKey -Name $n -Value ([int]$old.$n) }
        }
        Remove-Item $CrashControlSaved -Force
        Add-Line $log "crash dump settings restored: CrashDumpEnabled $($old.CrashDumpEnabled), AutoReboot $($old.AutoReboot)"
    }
    Get-ChildItem Cert:\LocalMachine\Root, Cert:\LocalMachine\TrustedPublisher |
        Where-Object { $_.Subject -eq 'CN=MousePods Test Signing' } | Remove-Item -Force
    Unregister-ResumeTask
    Remove-Item $ActivePath, $MarkerPath -Force -ErrorAction SilentlyContinue
    Say (Get-Content $log -Raw)
    Say 'MousePods driver, root device, certificates and logon task removed.' Green
    Close-Window
    exit 0
}

# Unfinished round: resume it (the logon task, or Henning running round.ps1 again).
if (-not $Resume -and (Test-Path $ActivePath)) {
    $answer = Read-Host 'An unfinished round exists. Continue it? [Y/n]'
    if ($answer -notmatch '^[nN]') { $Resume = $true }
    else {
        Remove-Item $ActivePath, $MarkerPath -Force -ErrorAction SilentlyContinue
        Unregister-ResumeTask
        Set-DriverStart 3       # no round is going on; a new one sets its route up itself
    }
}

if ($Resume) {
    if (-not (Test-Path $ActivePath)) {
        Set-DriverStart 3
        Unregister-ResumeTask
        Say 'No unfinished MousePods round; nothing to do.' Green
        Start-Sleep -Seconds 5
        exit 0
    }
    $round = Get-Content $ActivePath -Raw | ConvertFrom-Json
    $script:Addr = $round.addr
    $script:KeepDumps = [bool]$round.keep_dump
    $script:LogFile = Join-Path $round.dir 'round.log'
    Banner @('MousePods VM round: going on after a restart.') Cyan
    try {
        if ($FromTask -and -not $env:SAFEBOOT_OPTION) {
            Say 'Waiting 20 seconds for Windows to settle ...'
            Start-Sleep -Seconds 20
        }
        Resume-AfterCrash $round
        if ($round.phase -eq 'variants') {
            $hung = Clear-Routes (Join-Path $round.dir 'install.txt')
            if ($hung) { Restart-ForStuckDevice $round $hung }
        }
        if ($round.phase -ne 'variants') { Stop-Round $round "Windows went down during the $($round.phase) phase, before the variants (see crash.txt)." }
        Run-Steps $round
    } catch {
        Stop-Round $round "round.ps1 failed: $($_.Exception.Message) at line $($_.InvocationInfo.ScriptLineNumber)"
    }
    exit 0
}

# --- a new round ----------------------------------------------------------------

$stamp = Get-Date -Format 'yyyy-MM-dd-HHmm'
$roundDir = Join-Path $Data "rounds\$stamp"
New-Item -ItemType Directory -Force -Path $roundDir | Out-Null
$round = [ordered]@{ stamp = $stamp; dir = $roundDir; phase = 'setup'; addr = $null; name = $null; pkg = $null
    commit = $null; keep_dump = [bool]$KeepDump }
$round = [pscustomobject]$round
$script:KeepDumps = [bool]$KeepDump
$script:LogFile = Join-Path $roundDir 'round.log'
Banner @("MousePods VM round $stamp", 'Checking the setup first.') Cyan

try {
    if (-not $script:Python) { Stop-Round $round 'Python 3.10 or newer was not found. Install Python 3.12 (README step 6) and run round.ps1 again.' }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Stop-Round $round 'Git was not found. Install Git (README step 6) and run round.ps1 again.' }
    $null = Invoke-Py @('init', '--dir', $roundDir)

    # The checked-out commit, and whether the fork has a newer one.
    $head = (Invoke-Git @('rev-parse', 'HEAD')).Out[0].Trim()
    $round.commit = $head
    if (-not $SkipUpdateCheck) {
        $f = Invoke-Git @('fetch', '-q', 'origin', $Branch)
        if ($f.Code -eq 0) {
            $remote = (Invoke-Git @('rev-parse', 'FETCH_HEAD')).Out[0].Trim()
            $behind = (Invoke-Git @('merge-base', '--is-ancestor', $head, $remote)).Code -eq 0
            if ($remote -ne $head -and $behind) {
                Stop-Round $round "This copy is behind the fork ($($head.Substring(0,7)) here, $($remote.Substring(0,7)) on GitHub). Run 'git pull' in $Repo, then round.ps1 again."
            }
        } else { Say "Could not check GitHub for a newer commit: $($f.Text)" Yellow }
    }

    $facts = Get-SetupFacts
    $facts['Commit'] = "$head ($((Invoke-Git @('rev-parse', '--abbrev-ref', 'HEAD')).Out[0]))"
    Write-Setup (Join-Path $roundDir 'setup.txt') $facts @()
    $ts = Get-TestSigning
    if (-not $ts.Active) {
        if ($ts.Set) { Stop-Round $round 'Test signing is set but not active yet: restart Windows, then run round.ps1 again.' }
        Stop-Round $round "Test signing is off. In an administrator PowerShell run: bcdedit /set testsigning on - then restart Windows and run round.ps1 again (README step 5). If bcdedit says the value is protected by Secure Boot, turn Secure Boot off first (README step 2)."
    }
    if ($facts['HVCI (memory integrity)'] -like 'running: True*') {
        Say 'Memory integrity (HVCI) is on. The driver may be blocked; turn it off (README step 5) if the variants show route_not_loaded.' Yellow
    }

    # Crash dumps on, automatic restart after a bluescreen.
    $ccKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl'
    $cc = Get-ItemProperty $ccKey -ErrorAction SilentlyContinue
    if ($cc -and -not (Test-Path $CrashControlSaved)) {
        # kept locally, restored by -Uninstall
        Set-Content -Path $CrashControlSaved -Encoding UTF8 -Value (@{ CrashDumpEnabled = $cc.CrashDumpEnabled; AutoReboot = $cc.AutoReboot } | ConvertTo-Json)
    }
    if ($cc -and $cc.CrashDumpEnabled -eq 0) { Set-ItemProperty $ccKey -Name CrashDumpEnabled -Value 7 }
    Set-ItemProperty $ccKey -Name AutoReboot -Value 1

    # The deploy key must work before anything else happens, or the report could not come back.
    if (-not (Test-Path $KeyPath)) {
        Stop-Round $round "The VM deploy key $KeyPath is missing. Create it and add it to GitHub (README step 7), then run round.ps1 again."
    }
    Use-DeployKey
    $ls = Invoke-Native 'git' @('ls-remote', $PushUrl, 'vm-results')
    if ($ls.Code -ne 0) {
        Stop-Round $round "GitHub does not accept the VM deploy key (README step 7): $($ls.Text)"
    }

    # The AirPods: from the paired devices, or -Address.
    $devs = @(Last-Json (Invoke-Py @('devices')))
    if ($Address) {
        $round.addr = ($Address.Trim() -replace '-', ':').ToUpper()
        if ($round.addr -notmatch '^([0-9A-F]{2}:){5}[0-9A-F]{2}$') {
            Stop-Round $round "-Address must look like AA:BB:CC:DD:EE:FF (got $Address)."
        }
        $named = @($devs | Where-Object { $_.address -eq $round.addr })
        $round.name = if ($named) { $named[0].name } else { '(not in the paired list)' }
    } else {
        $pick = $null
        $pods = @($devs | Where-Object { $_ -and $_.name -like '*AirPods*' })
        $pick = @($pods | Where-Object { $_.connected }) + $pods | Select-Object -First 1
        if (-not $pick) {
            Stop-Round $round 'No AirPods in the paired Bluetooth devices. Pair them in Windows (README step 4) or pass -Address AA:BB:CC:DD:EE:FF.'
        }
        $round.addr = $pick.address
        $round.name = $pick.name
    }
    $script:Addr = $round.addr
    Say "AirPods: $($round.name) ($(Get-MaskedAddr $round.addr))" Green
    $audio = $facts['Default audio output']
    if ($audio -notlike '*AirPods*' -or $audio -like '*Hands-Free*') {
        Banner @("Windows plays sound on: $audio",
            'Make the AirPods the output: Settings > System > Sound > Output > AirPods (not Hands-Free).',
            'Press Enter when done, or wait 60 seconds to go on anyway.')
        Wait-Enter 60
        try { $audio = [MousePodsVm.Helpers]::DefaultOutput() } catch { }
        $facts['Default audio output'] = $audio
    }

    # The driver CI built for this commit (or for the newest commit with the same driver sources).
    $f = Fetch-Results 'origin'
    if (-not $f.Sha) { $f = Fetch-Results $PushUrl }
    if (-not $f.Sha) { Stop-Round $round "Could not fetch the vm-results branch: $($f.Text)" }
    if (-not (Reset-ResultsTree $f.Sha)) { Stop-Round $round "Could not check out vm-results into $ResultsTree." }
    $built = @{}
    foreach ($e in (Invoke-Git @('ls-tree', '--name-only', 'HEAD', 'ci/') $ResultsTree).Out) { $built[(Split-Path $e -Leaf)] = $true }
    $short = $head.Substring(0, 7)
    $pkgSha = $null
    foreach ($c in (Invoke-Git @('rev-list', '--max-count=100', 'HEAD')).Out) {
        $s = $c.Substring(0, 7)
        if ($built.ContainsKey($s) -and (Test-Path (Join-Path $ResultsTree "ci\$s\driver.zip"))) { $pkgSha = $s; break }
    }
    $note = ''
    if ($pkgSha -and $pkgSha -ne $short) {
        $same = (Invoke-Git @('diff', '--quiet', $pkgSha, $head, '--', 'windows/driver')).Code -eq 0
        if (-not $same) { $pkgSha = $null } else { $note = " (driver sources unchanged since $pkgSha)" }
    }
    if (-not $pkgSha) {
        $failed = @(Get-ChildItem (Join-Path $ResultsTree 'ci') -Filter "*-$short.md" -ErrorAction SilentlyContinue)
        if ($failed) {
            Stop-Round $round "GitHub Actions built commit $short but produced no driver (see ci/$($failed[0].Name) on vm-results)."
        }
        Stop-Round $round "GitHub Actions has not built commit $short yet. Wait until the 'windows-driver' run on GitHub has finished (about 10 minutes), then run round.ps1 again."
    }
    $pkg = Join-Path $Data "driver\$pkgSha"
    if (Test-Path $pkg) { Remove-Item -Recurse -Force $pkg }
    Expand-Archive -Path (Join-Path $ResultsTree "ci\$pkgSha\driver.zip") -DestinationPath $pkg -Force
    $round.pkg = $pkg
    $facts['Driver package'] = "ci/$pkgSha/driver.zip$note"
    $facts['AirPods'] = "$($round.name) ($($round.addr))"
    Write-Setup (Join-Path $roundDir 'setup.txt') $facts $devs
    $meta = Join-Path $roundDir 'meta.json'
    Set-Content -Path $meta -Encoding UTF8 -Value (@{ stamp = $stamp; commit = $head; driver = "ci/$pkgSha/driver.zip$note"
            addr = (Get-MaskedAddr $round.addr); name = $round.name; started = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') } | ConvertTo-Json)
    $null = Invoke-Py @('init', '--dir', $roundDir, '--meta', $meta, '--known', $round.addr)
    Remove-Item $meta -Force

    # Install: old packages out, certificate in, new package into the driver store.
    $round.phase = 'install'
    Save-Round $round
    Register-ResumeTask
    $install = Join-Path $roundDir 'install.txt'
    Say 'Installing the driver ...' Cyan
    $hung = Remove-OldDrivers $install
    if ($hung) { Stop-Round $round "Removing the old driver did not finish ($hung). Restart Windows and run round.ps1 again." }
    $cer = Join-Path $pkg 'MousePodsL2cap.cer'
    $inf = Join-Path $pkg 'MousePodsL2cap.inf'
    foreach ($f in @($cer, $inf, (Join-Path $pkg 'MousePodsL2cap.sys'), (Join-Path $pkg 'MousePodsL2cap.cat'))) {
        if (-not (Test-Path $f)) { Stop-Round $round "The driver package has no $(Split-Path $f -Leaf)." }
    }
    $newCert = New-Object Security.Cryptography.X509Certificates.X509Certificate2 $cer
    Get-ChildItem Cert:\LocalMachine\Root, Cert:\LocalMachine\TrustedPublisher |
        Where-Object { $_.Subject -eq 'CN=MousePods Test Signing' -and $_.Thumbprint -ne $newCert.Thumbprint } | Remove-Item -Force
    foreach ($store in @('Cert:\LocalMachine\Root', 'Cert:\LocalMachine\TrustedPublisher')) {
        $null = Import-Certificate -FilePath $cer -CertStoreLocation $store
    }
    Add-Line $install "certificate $($newCert.Subject) $($newCert.Thumbprint) imported into Root and TrustedPublisher"
    $r = Invoke-Timed 'pnputil.exe' @('/add-driver', $inf, '/install') $InstallTimeoutS
    Add-Line $install "pnputil /add-driver $inf /install -> exit $($r.Code)`n$($r.Text)"
    if ($r.TimedOut) { Stop-Round $round "pnputil /add-driver did not finish in $InstallTimeoutS s. Restart Windows and run round.ps1 again." }
    if (@(0, 259, 3010) -notcontains $r.Code) { Stop-Round $round "pnputil could not add the driver (exit $($r.Code)): $($r.Text)" }
    $round.phase = 'variants'
    Save-Round $round
    if ($r.Code -eq 3010) {
        Banner @('Windows needs a restart to replace the old driver.', 'Restarting in 30 seconds; log in again and the round goes on by itself.')
        Start-Sleep -Seconds 30
        Restart-Computer -Force
        exit 0
    }
    Run-Steps $round
} catch {
    Stop-Round $round "round.ps1 failed: $($_.Exception.Message) at line $($_.InvocationInfo.ScriptLineNumber)"
}
