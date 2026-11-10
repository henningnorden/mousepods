# MousePods on Windows: L2CAP driver

Windows gives programs no way to open the Bluetooth L2CAP channel the AirPods use for their
motion data (AAP, PSM 0x1001). `driver/` is a small KMDF kernel driver that opens exactly that
channel to a paired device and passes whole packets to and from user mode; all protocol handling
stays in the Python code shared with Linux (`l2cap_win.py`).

- Only PSM 0x1001 is accepted; any other PSM is refused before anything is sent.
- The device can be opened by the system, administrators and the logged-in user only.
- One channel at a time; a second connect gets `STATUS_DEVICE_BUSY`.
- The driver keeps a 16 KiB text log (`IOCTL_MPL2_LOG`) of every state change and error.

The interface between driver and Python is `driver/public.h`.

## Building

GitHub Actions builds the driver on every push to the `windows-l2cap-driver` branch
(`.github/workflows/windows-driver.yml`): WDK from NuGet, the compiler's static analysis
(`/analyze`), InfVerif, and test signing with a certificate made for that build. The report and
the test-signed `driver.zip` land on the `vm-results` branch under `ci/`.

Locally on Windows (Visual Studio 2026 with the C++ workload, `nuget.exe` on `PATH`; WDK
10.0.28000 from NuGet, whose build tasks need MSBuild 18):

    pwsh windows/driver/build.ps1

The driver is test-signed only; it loads with `bcdedit /set testsigning on`.

## Testing in a Windows VM

One test round runs every open variant in one go: both ways of loading the driver (route a, a
root device; route b, a Bluetooth service on the AirPods), two receive MTUs (2582, 672), with and
without encryption, with and without Windows playing audio to the AirPods: 16 runs of 15 seconds,
then four checks (a wrong PSM is refused, a second handle is refused, a killed program's channel
is cleaned up, a dropped link is reported). It takes about 10 minutes. At the end the script
pushes its report to the `vm-results` branch, where it can be read without logging in to the VM.

You only do steps 1 to 7 once.

### 1. Virtualization on Bazzite

In a terminal on Bazzite:

    ujust setup-virtualization

Choose "Enable Virtualization", restart when it asks, then run it again and pick "Add user to
libvirt group" if it offers that. Open **Virtual Machine Manager** (virt-manager) from the app menu.

### 2. The Windows 11 VM

Download the Windows 11 ISO from microsoft.com ("Download Windows 11 Disk Image (ISO)").

In virt-manager: File > New Virtual Machine > Local install media > choose the ISO. Memory
**8192 MB**, CPUs **4**, disk 80 GB. On the last page tick **Customize configuration before
install**, then:

- Overview > Firmware: pick the **UEFI** entry *without* `secboot` / `secure-boot` in its name.
  Secure Boot must be off, or Windows refuses test signing.
- Add Hardware > TPM: type **Emulated**, model CRB, version 2.0.
- Begin Installation.

If Windows Setup says "This PC can't run Windows 11": press Shift+F10, type

    reg add HKLM\SYSTEM\Setup\LabConfig /v BypassSecureBootCheck /t REG_DWORD /d 1 /f

close the window, click the back arrow once and go on.

### 3. The Bluetooth stick into the VM

With the VM shut down: open it in virt-manager > the light bulb (Show virtual hardware details) >
Add Hardware > USB Host Device > pick the Bluetooth stick > Finish.

While the VM runs, **Bazzite has no Bluetooth**: controllers and headsets connected to Bazzite
drop. Shut the VM down to get it back. After the AirPods were paired in Windows, Bazzite may have
to pair them again (same stick, new pairing key).

### 4. Pair the AirPods in Windows

Settings > Bluetooth & devices > Add device > Bluetooth. Open the AirPods case next to the PC and
hold the button on the back until the light blinks white; pick the AirPods.

Then make them the sound output: Settings > System > Sound > Output > **AirPods** (the entry
without "Hands-Free"). Play any sound to check it comes out of the AirPods.

### 5. Test signing on, memory integrity off

Start > type `powershell` > right-click **Windows PowerShell** > Run as administrator:

    bcdedit /set testsigning on

Then Windows Security > Device security > Core isolation details > **Memory integrity: Off**.
Restart Windows. "Test Mode" in the bottom right corner of the desktop means it worked.

### 6. Python and Git, and the code

In a normal (not administrator) PowerShell:

    winget install -e --id Python.Python.3.12
    winget install -e --id Git.Git

Close and reopen PowerShell, then fetch the code (read-only, no login needed):

    git clone -b windows-l2cap-driver https://github.com/henningnorden/mousepods.git $HOME\mousepods

Optional, for bluescreen analysis: install the Windows SDK
(`winget install -e --id Microsoft.WindowsSDK.10.0.26100`) with "Debugging Tools for Windows".
Without it, a bluescreen is still recorded, only without the analysis.

### 7. The VM deploy key

The VM pushes its reports with its own key, which can write to the fork but not to `main` or
the `v*` tags. In PowerShell:

    ssh-keygen -t ed25519 -C "mousepods vm" -f $HOME\.ssh\mousepods_vm

Press Enter twice (no passphrase). Then copy the public key:

    Get-Content $HOME\.ssh\mousepods_vm.pub | Set-Clipboard

On GitHub, in the fork **henningnorden/mousepods** > Settings > Deploy keys > Add deploy key:
title `MousePods VM`, paste the key, tick **Allow write access**, Add key.

Do **not** add this key to the bypass list of the fork's ruleset (Settings > Rules > Rulesets):
the ruleset is what keeps `main` and the tags safe from it.

### 8. Run a round

Before each round: wear the AirPods and make sure they play Windows' sound. Then, in PowerShell:

    cd $HOME\mousepods
    git pull
    powershell -ExecutionPolicy Bypass -File windows\vm\round.ps1

Windows asks for administrator rights; say yes. A new window does the rest:

- it checks the setup and stops with a clear message if something is missing (for example test
  signing, or GitHub Actions has not built the driver for the latest commit yet: wait about 10
  minutes after a push and try again),
- it installs the driver GitHub Actions built for this commit,
- it runs the variants. **Turn your head slowly left and right now and then.** Some variants play
  a quiet beep tone; that is intended,
- near the end it asks you to **put both AirPods into the case and close the lid** within 20
  seconds,
- it pushes the report and says so. Then you are done.

If the script picks the wrong AirPods, pass the address yourself:
`... round.ps1 -Address AA:BB:CC:DD:EE:FF`.

### 9. After a bluescreen

Nothing to do but **log in again**. Windows restarts by itself; after you log in, the round goes
on with the next variant on its own (give it 20 seconds). The bluescreen is recorded in the
report with the crash dump.

If nothing happens after logging in, double-click **Continue MousePods round** on the desktop.

If Windows bluescreens again while starting (before you can log in), the driver crashes as soon
as Windows loads it. After two failed starts Windows shows a blue "Choose an option" screen:
Troubleshoot > Advanced options > Startup Settings > Restart > press **4** (Safe Mode). Log in and
double-click **Continue MousePods round** on the desktop. It switches the driver off, records the
crash and asks you to restart normally; after that the round goes on without the variants that
would crash the same way.

### 10. Afterwards

- If the report could not be pushed (no network, key problem), the window says where the round
  folder is. Push it later with
  `powershell -ExecutionPolicy Bypass -File windows\vm\round.ps1 -Push <that folder>`.
- To remove the driver, the root device, the test certificates and the logon task:
  `powershell -ExecutionPolicy Bypass -File windows\vm\round.ps1 -Uninstall`.

What is where: `vm/round.ps1` is the entry point, `vm/devnode.ps1` creates and removes the root
device (what `devcon` does), `vm/run_variants.py` has the variant list, the checks and the
report. The round folder on `vm-results` is `rounds/<date-time>/` with `report.md` (outcome
first, failures first), `results.json`, `driver.log`, `setup.txt`, `install.txt`, `round.log`,
`steps.log` and, after a bluescreen, `crash.txt` and the minidump.

## Credits

The BRB handling, the continuous reader and the close sequence are based on Microsoft's
[Windows-driver-samples](https://github.com/microsoft/Windows-driver-samples)
`bluetooth/bthecho` sample, MIT licence, Copyright (c) Microsoft Corporation. No code from
MagicAAP or any other closed driver is used.
