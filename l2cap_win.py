# SPDX-License-Identifier: Apache-2.0
"""Python mirror of windows/driver/public.h (constants and ctypes structs).

Windows-only lookups (ctypes.windll) must stay inside functions, never at import.

The Win32 calls go through an injectable ``api`` object (see _Win32 for the
contract); tests pass a fake, the default is the real kernel32/cfgmgr32.
"""
import ctypes
import errno
import queue
import socket
import threading
import uuid

GUID_DEVINTERFACE_MOUSEPODS_L2CAP = "6d0e3c52-4f1b-4c9a-9a51-2f0b8e7d1a01"

PSM_AAP = 0x1001
VERSION = 1

_FILE_DEVICE_UNKNOWN = 0x22


def _ctl_code(function, access):
    # METHOD_BUFFERED is 0
    return (_FILE_DEVICE_UNKNOWN << 16) | (access << 14) | (function << 2)


_READ, _WRITE = 1, 2

IOCTL_INFO = _ctl_code(0x800, _READ)
IOCTL_CONNECT = _ctl_code(0x801, _READ | _WRITE)
IOCTL_DISCONNECT = _ctl_code(0x802, 0)
IOCTL_LOG = _ctl_code(0x803, _READ)

STAGE_NAMES = {
    1: "interface/radio lookup",
    2: "BRB submit",
    3: "remote response",
    4: "configuration",
    5: "open",
}

SECURITY = {"none": 0, "auth": 1, "encrypt": 2}


class Info(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("route", ctypes.c_uint32),  # 1 root, 2 bthenum
        ("radio_addr", ctypes.c_uint64),
    ]


class ConnectIn(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("bth_addr", ctypes.c_uint64),
        ("psm", ctypes.c_uint16),
        ("mtu", ctypes.c_uint16),
        ("security", ctypes.c_uint32),
        ("timeout_ms", ctypes.c_uint32),
    ]


class ConnectOut(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("ntstatus", ctypes.c_int32),
        ("bt_status", ctypes.c_uint32),
        ("stage", ctypes.c_uint32),
        ("out_mtu", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16),
    ]


def addr_to_u64(addr):
    """'AA:BB:CC:DD:EE:FF' -> 0xAABBCCDDEEFF."""
    parts = addr.split(":")
    if len(parts) != 6:
        raise ValueError("not a Bluetooth address: %r" % (addr,))
    try:
        octets = [int(p, 16) for p in parts]
    except ValueError:
        raise ValueError("not a Bluetooth address: %r" % (addr,)) from None
    if any(len(p) != 2 or not 0 <= o <= 255 for p, o in zip(parts, octets)):
        raise ValueError("not a Bluetooth address: %r" % (addr,))
    value = 0
    for o in octets:
        value = (value << 8) | o
    return value


# --- Win32 error codes the driver's NTSTATUS values arrive as ----------------
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_IO_PENDING = 997
ERROR_BUSY = 170
ERROR_DEVICE_NOT_CONNECTED = 1167
ERROR_OPERATION_ABORTED = 995

INVALID_HANDLE = -1

_LOG_BUFFER = 32768
_ROUTES = {1: "root", 2: "bthenum"}


class DriverError(OSError):
    """The driver reported a failure; stage 0 means refused before anything was sent."""

    def __init__(self, ntstatus, bt_status, stage, winerror=None):
        what = STAGE_NAMES.get(stage, "refused before sending" if stage == 0 else "stage %d" % stage)
        if winerror is not None:
            msg = "L2CAP driver: %s (Win32 error %d)" % (what, winerror)
        else:
            msg = "L2CAP driver: %s failed (NTSTATUS 0x%08X, Bluetooth status 0x%X)" % (
                what, ntstatus & 0xFFFFFFFF, bt_status)
        super().__init__(msg)
        # after OSError.__init__: on Windows it resets the winerror slot
        self.ntstatus = ntstatus
        self.bt_status = bt_status
        self.stage = stage
        self.winerror = winerror


def _raise_win(err, what):
    if err == ERROR_ACCESS_DENIED:
        raise PermissionError(errno.EACCES, "%s: access denied" % what)
    if err == ERROR_FILE_NOT_FOUND:
        raise FileNotFoundError(errno.ENOENT, "%s: not found" % what)
    raise OSError(errno.EIO, "%s failed (Win32 error %d)" % (what, err))


def find_device_path(api=None):
    api = api or default_api()
    paths = api.list_device_paths(GUID_DEVINTERFACE_MOUSEPODS_L2CAP)
    if not paths:
        raise FileNotFoundError(errno.ENOENT, "MousePods L2CAP driver is not installed")
    return paths[0]


def _open_device(api):
    path = find_device_path(api)
    handle = api.CreateFileW(path)
    if handle == INVALID_HANDLE:
        _raise_win(api.GetLastError(), "opening the L2CAP driver")
    return handle


def driver_info(api=None):
    api = api or default_api()
    handle = _open_device(api)
    try:
        out = api.DeviceIoControl(handle, IOCTL_INFO, None, ctypes.sizeof(Info))
        if out is None:
            _raise_win(api.GetLastError(), "driver info")
        info = Info.from_buffer_copy(out)
    finally:
        api.CloseHandle(handle)
    raw = info.radio_addr
    radio = ":".join("%02X" % ((raw >> shift) & 0xFF) for shift in range(40, -8, -8))
    return {"version": info.version, "route": _ROUTES.get(info.route, "none"), "radio": radio}


def driver_log(api=None):
    api = api or default_api()
    handle = _open_device(api)
    try:
        out = api.DeviceIoControl(handle, IOCTL_LOG, None, _LOG_BUFFER)
        if out is None:
            _raise_win(api.GetLastError(), "driver log")
    finally:
        api.CloseHandle(handle)
    return bytes(out).decode("utf-8", "replace")


class Channel:
    """One L2CAP channel, shaped like the Linux SEQPACKET socket (recv, send,
    settimeout, fileno for select, close). A reader thread moves each packet
    into a queue and writes one byte to a socketpair so select() wakes."""

    def __init__(self, api, handle, read_size, connect_out):
        self._api = api
        self._handle = handle
        self._read_size = read_size
        self.connect_out = connect_out
        self.dropped = 0
        self._timeout = None
        self._queue = queue.Queue()
        self._error = None
        self._closing = False
        self._closed = False
        self._rd, self._wr = socket.socketpair()
        self._thread = threading.Thread(target=self._read_loop, name="l2cap-win-reader", daemon=True)
        self._thread.start()

    def _read_loop(self):
        api = self._api
        while not self._closing:
            data = api.ReadFile(self._handle, self._read_size)
            if data is not None:
                self._deliver(bytes(data))
                continue
            err = api.GetLastError()
            if self._closing or err == ERROR_OPERATION_ABORTED:
                return
            if err == ERROR_INSUFFICIENT_BUFFER:
                self.dropped += 1
                continue
            self._deliver(self._read_error(err))
            return

    @staticmethod
    def _read_error(err):
        if err == ERROR_DEVICE_NOT_CONNECTED:
            return ConnectionResetError(errno.ECONNRESET, "L2CAP channel closed")
        return OSError(errno.EIO, "L2CAP read failed (Win32 error %d)" % err)

    def _deliver(self, item):
        self._queue.put(item)
        try:
            self._wr.send(b"\0")
        except OSError:
            pass                                   # closing

    def fileno(self):
        return self._rd.fileno()

    def settimeout(self, t):
        self._timeout = t

    def gettimeout(self):
        return self._timeout

    def recv(self, bufsize):
        if self._error is not None:
            raise self._error
        if self._closed:
            raise OSError(errno.EBADF, "channel is closed")
        try:
            if self._timeout is None:
                item = self._queue.get()
            elif self._timeout == 0:
                item = self._queue.get_nowait()
            else:
                item = self._queue.get(timeout=self._timeout)
        except queue.Empty:
            if self._timeout == 0:
                raise BlockingIOError(errno.EAGAIN, "no packet waiting") from None
            raise socket.timeout("timed out") from None
        if isinstance(item, Exception):
            # leave the wake-up byte in place so select() keeps reporting it
            self._error = item
            raise item
        self._rd.recv(1)
        return item[:bufsize]

    def send(self, data):
        data = bytes(data)
        if self._closed:
            raise OSError(errno.EBADF, "channel is closed")
        n = self._api.WriteFile(self._handle, data)
        if n is None:
            err = self._api.GetLastError()
            if err == ERROR_DEVICE_NOT_CONNECTED:
                raise ConnectionResetError(errno.ECONNRESET, "L2CAP channel closed")
            raise OSError(errno.EIO, "L2CAP write failed (Win32 error %d)" % err)
        return n

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._closing = True
        api = self._api
        api.DeviceIoControl(self._handle, IOCTL_DISCONNECT, None, 0)
        api.CancelIoEx(self._handle)
        self._thread.join(5)
        api.CloseHandle(self._handle)
        self._rd.close()
        self._wr.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def open_seqpacket(addr, psm, mtu, timeout, security="encrypt", api=None):
    if psm != PSM_AAP:
        raise PermissionError(errno.EACCES, "the L2CAP driver only opens PSM 0x%04X" % PSM_AAP)
    bth = addr_to_u64(addr)
    sec = SECURITY[security]
    api = api or default_api()
    handle = _open_device(api)
    try:
        req = ConnectIn(bth, psm, mtu, sec, int(timeout * 1000) if timeout else 0)
        out = api.DeviceIoControl(handle, IOCTL_CONNECT, bytes(req), ctypes.sizeof(ConnectOut))
        if out is None:
            err = api.GetLastError()
            if err == ERROR_ACCESS_DENIED:
                raise PermissionError(errno.EACCES, "the L2CAP driver refused the connection (access denied)")
            raise DriverError(0, 0, 0, winerror=err)
        co = ConnectOut.from_buffer_copy(out)
        if co.ntstatus != 0:
            raise DriverError(co.ntstatus, co.bt_status, co.stage)
        fields = {name: getattr(co, name) for name, _ in ConnectOut._fields_}
        return Channel(api, handle, co.out_mtu or mtu, fields)
    except BaseException:
        api.CloseHandle(handle)
        raise


# --- the real Win32 API (only touched on Windows) ----------------------------

def default_api():
    return _Win32()


class _Win32:
    """The calls l2cap_win needs, over kernel32/cfgmgr32. Contract (the fakes in
    the tests follow it): failing calls return None (CreateFileW: INVALID_HANDLE)
    and the Win32 error is then in GetLastError() of the same thread. Handles are
    opened overlapped so a blocked ReadFile does not hold up WriteFile."""

    def __init__(self):
        from ctypes import wintypes
        self._w = wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._cm = ctypes.WinDLL("cfgmgr32", use_last_error=True)
        self._k = k
        self._tls = threading.local()
        H, D, P = wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, D, D, P, D, D, H]
        k.CreateFileW.restype = P
        k.CreateEventW.argtypes = [P, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        k.CreateEventW.restype = P
        k.DeviceIoControl.argtypes = [P, D, P, D, P, D, ctypes.POINTER(D), P]
        k.DeviceIoControl.restype = wintypes.BOOL
        k.ReadFile.argtypes = [P, P, D, ctypes.POINTER(D), P]
        k.ReadFile.restype = wintypes.BOOL
        k.WriteFile.argtypes = [P, P, D, ctypes.POINTER(D), P]
        k.WriteFile.restype = wintypes.BOOL
        k.GetOverlappedResult.argtypes = [P, P, ctypes.POINTER(D), wintypes.BOOL]
        k.GetOverlappedResult.restype = wintypes.BOOL
        k.CancelIoEx.argtypes = [P, P]
        k.CancelIoEx.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [P]
        k.CloseHandle.restype = wintypes.BOOL

    class _Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", ctypes.c_uint32), ("OffsetHigh", ctypes.c_uint32),
                    ("hEvent", ctypes.c_void_p)]

    def GetLastError(self):
        return getattr(self._tls, "err", 0)

    def _fail(self, err=None):
        self._tls.err = ctypes.get_last_error() if err is None else err
        return None

    def list_device_paths(self, guid_text):
        w = self._w

        class GUID(ctypes.Structure):
            _fields_ = [("d", ctypes.c_byte * 16)]

        g = GUID.from_buffer_copy(uuid.UUID(guid_text).bytes_le)
        size = w.ULONG(0)
        cm = self._cm
        if cm.CM_Get_Device_Interface_List_SizeW(ctypes.byref(size), ctypes.byref(g), None, 0) != 0:
            return []
        buf = ctypes.create_unicode_buffer(size.value)
        if cm.CM_Get_Device_Interface_ListW(ctypes.byref(g), None, buf, size, 0) != 0:
            return []
        return [p for p in ctypes.wstring_at(ctypes.addressof(buf), size.value).split("\0") if p]

    def CreateFileW(self, path):
        GENERIC_RW, SHARE_RW, OPEN_EXISTING, OVERLAPPED = 0xC0000000, 3, 3, 0x40000000
        h = self._k.CreateFileW(path, GENERIC_RW, SHARE_RW, None, OPEN_EXISTING, OVERLAPPED, None)
        if h in (None, 0, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFF):
            self._fail()
            return INVALID_HANDLE
        return h

    def _overlapped(self, handle, start):
        """Run start(ov, nbytes_ptr) as an overlapped call and wait for it."""
        w = self._w
        ev = self._k.CreateEventW(None, True, False, None)
        ov = self._Overlapped()
        ov.hEvent = ev
        n = w.DWORD(0)
        try:
            ok = start(ctypes.byref(ov), ctypes.byref(n))
            if not ok:
                err = ctypes.get_last_error()
                if err != ERROR_IO_PENDING:
                    return self._fail(err)
            if not self._k.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(n), True):
                return self._fail()
            return n.value
        finally:
            self._k.CloseHandle(ev)

    def DeviceIoControl(self, handle, code, data, out_size):
        inb = ctypes.create_string_buffer(bytes(data), len(data)) if data else None
        outb = ctypes.create_string_buffer(out_size) if out_size else None
        n = self._overlapped(handle, lambda ov, nb: self._k.DeviceIoControl(
            handle, code, inb, len(data) if data else 0, outb, out_size, nb, ov))
        if n is None:
            return None
        return outb.raw[:n] if outb else b""

    def ReadFile(self, handle, size):
        buf = ctypes.create_string_buffer(size)
        n = self._overlapped(handle, lambda ov, nb: self._k.ReadFile(handle, buf, size, nb, ov))
        return None if n is None else buf.raw[:n]

    def WriteFile(self, handle, data):
        buf = ctypes.create_string_buffer(data, len(data))
        return self._overlapped(handle, lambda ov, nb: self._k.WriteFile(handle, buf, len(data), nb, ov))

    def CancelIoEx(self, handle):
        return bool(self._k.CancelIoEx(handle, None))

    def CloseHandle(self, handle):
        return bool(self._k.CloseHandle(handle))
