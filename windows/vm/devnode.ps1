# SPDX-License-Identifier: Apache-2.0
# The root-enumerated device for route a of the MousePods L2CAP driver: what
# `devcon install <inf> Root\MousePodsL2cap` does, through SetupAPI, so the VM
# needs no extra tool. Run as administrator (round.ps1 calls it).
#
#   devnode.ps1 -Action create -InfPath <...\MousePodsL2cap.inf>
#   devnode.ps1 -Action remove
#   devnode.ps1 -Action status
#   devnode.ps1 -Action compile      only compiles the SetupAPI declarations (CI)
#
# Exit code 0 when the action worked, 1 otherwise; what happened is printed.

param(
    [ValidateSet('create', 'remove', 'status', 'compile')]
    [string]$Action = 'status',
    [string]$InfPath
)

$ErrorActionPreference = 'Stop'
$HardwareId = 'Root\MousePodsL2cap'

$source = @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

public static class MousePodsDevnode
{
    [StructLayout(LayoutKind.Sequential)]
    struct SP_DEVINFO_DATA
    {
        public int cbSize;
        public Guid ClassGuid;
        public int DevInst;
        public IntPtr Reserved;
    }

    [DllImport("setupapi.dll", SetLastError = true)]
    static extern IntPtr SetupDiCreateDeviceInfoList(ref Guid classGuid, IntPtr hwnd);

    [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool SetupDiCreateDeviceInfoW(IntPtr set, string name, ref Guid classGuid,
        string description, IntPtr hwnd, int flags, ref SP_DEVINFO_DATA data);

    [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool SetupDiSetDeviceRegistryPropertyW(IntPtr set, ref SP_DEVINFO_DATA data,
        int property, byte[] buffer, int size);

    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiCallClassInstaller(int function, IntPtr set, ref SP_DEVINFO_DATA data);

    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiDestroyDeviceInfoList(IntPtr set);

    [DllImport("newdev.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool UpdateDriverForPlugAndPlayDevicesW(IntPtr hwnd, string hardwareId,
        string infPath, int flags, out bool rebootRequired);

    const int DICD_GENERATE_ID = 1;
    const int SPDRP_HARDWAREID = 1;
    const int DIF_REMOVE = 5;
    const int DIF_REGISTERDEVICE = 0x19;
    const int INSTALLFLAG_FORCE = 1;
    static readonly Guid SystemClass = new Guid("4d36e97d-e325-11ce-bfc1-08002be10318");

    // Registers ROOT\MOUSEPODSL2CAP\nnnn with the hardware ID and installs the
    // driver from the INF on it. Returns true when Windows asks for a restart.
    public static bool Create(string hardwareId, string infPath)
    {
        Guid cls = SystemClass;
        IntPtr set = SetupDiCreateDeviceInfoList(ref cls, IntPtr.Zero);
        if (set == new IntPtr(-1))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "SetupDiCreateDeviceInfoList");
        try
        {
            SP_DEVINFO_DATA data = new SP_DEVINFO_DATA();
            data.cbSize = Marshal.SizeOf(typeof(SP_DEVINFO_DATA));
            if (!SetupDiCreateDeviceInfoW(set, "MousePodsL2cap", ref cls, null, IntPtr.Zero,
                    DICD_GENERATE_ID, ref data))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "SetupDiCreateDeviceInfo");
            byte[] ids = Encoding.Unicode.GetBytes(hardwareId + "\0\0");
            if (!SetupDiSetDeviceRegistryPropertyW(set, ref data, SPDRP_HARDWAREID, ids, ids.Length))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "SetupDiSetDeviceRegistryProperty");
            if (!SetupDiCallClassInstaller(DIF_REGISTERDEVICE, set, ref data))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "DIF_REGISTERDEVICE");
            bool reboot;
            if (!UpdateDriverForPlugAndPlayDevicesW(IntPtr.Zero, hardwareId, infPath, INSTALLFLAG_FORCE,
                    out reboot))
            {
                int err = Marshal.GetLastWin32Error();
                SetupDiCallClassInstaller(DIF_REMOVE, set, ref data);   // leave no device without a driver
                throw new Win32Exception(err, "UpdateDriverForPlugAndPlayDevices");
            }
            return reboot;
        }
        finally
        {
            SetupDiDestroyDeviceInfoList(set);
        }
    }

    public static int StructSize()
    {
        return Marshal.SizeOf(typeof(SP_DEVINFO_DATA));
    }
}
'@

if (-not ('MousePodsDevnode' -as [type])) {
    Add-Type -TypeDefinition $source -Language CSharp
}

function Get-MousePodsDevnode {
    # present and non-present (phantom) devices with our hardware ID
    @(Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {
        $_.HardwareID -contains $HardwareId -or $_.InstanceId -like 'ROOT\MOUSEPODSL2CAP\*'
    })
}

switch ($Action) {
    'compile' {
        $size = [MousePodsDevnode]::StructSize()
        if ($size -ne 32) {
            Write-Host "::error title=devnode.ps1::SP_DEVINFO_DATA is $size bytes, expected 32 on x64"
            exit 1
        }
        Write-Host "devnode.ps1: SetupAPI declarations compiled, SP_DEVINFO_DATA $size bytes"
        exit 0
    }
    'status' {
        $devs = Get-MousePodsDevnode
        if (-not $devs) { Write-Host 'root device: none'; exit 0 }
        foreach ($d in $devs) { Write-Host "root device: $($d.InstanceId), status $($d.Status), problem $($d.Problem)" }
        exit 0
    }
    'create' {
        $existing = @(Get-MousePodsDevnode | Where-Object { $_.Status -eq 'OK' })
        if ($existing) {
            Write-Host "root device already there: $($existing[0].InstanceId)"
            exit 0
        }
        if (-not $InfPath -or -not (Test-Path $InfPath)) {
            Write-Host "devnode.ps1: INF not found: $InfPath"
            exit 1
        }
        try {
            $reboot = [MousePodsDevnode]::Create($HardwareId, (Resolve-Path $InfPath).Path)
        } catch {
            Write-Host "devnode.ps1: creating the root device failed: $($_.Exception.InnerException.Message) $($_.Exception.Message)"
            exit 1
        }
        foreach ($d in Get-MousePodsDevnode) { Write-Host "root device: $($d.InstanceId), status $($d.Status)" }
        if ($reboot) { Write-Host 'Windows asks for a restart to finish the install' }
        exit 0
    }
    'remove' {
        $failed = $false
        $ErrorActionPreference = 'Continue'     # pnputil output on stderr must not stop the script
        foreach ($d in Get-MousePodsDevnode) {
            $out = & pnputil.exe /remove-device $d.InstanceId 2>&1 | ForEach-Object { "$_" }
            Write-Host "remove $($d.InstanceId): exit $LASTEXITCODE $($out -join ' ')"
            if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 3010) { $failed = $true }
        }
        if ($failed) { exit 1 }
        exit 0
    }
}
