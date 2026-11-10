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

Locally on Windows (Visual Studio 2022 with the C++ workload, `nuget.exe` on `PATH`):

    pwsh windows/driver/build.ps1

The driver is test-signed only; it loads with `bcdedit /set testsigning on`.

## Credits

The BRB handling, the continuous reader and the close sequence are based on Microsoft's
[Windows-driver-samples](https://github.com/microsoft/Windows-driver-samples)
`bluetooth/bthecho` sample, MIT licence, Copyright (c) Microsoft Corporation. No code from
MagicAAP or any other closed driver is used.
