// SPDX-License-Identifier: Apache-2.0
// Ring log: 16 KiB of UTF-8 text lines, oldest dropped, each line starts with
// the time since boot in milliseconds. Read through IOCTL_MPL2_LOG.

#pragma once

#define MPL2_LOG_SIZE 16384

_IRQL_requires_max_(PASSIVE_LEVEL)
VOID
Mpl2LogInit(VOID);

_IRQL_requires_max_(DISPATCH_LEVEL)
VOID
Mpl2Log(_In_z_ _Printf_format_string_ PCSTR Format, ...);

// Copies the newest log text (up to Size bytes, oldest first) and returns the
// number of bytes copied.
_IRQL_requires_max_(DISPATCH_LEVEL)
ULONG
Mpl2LogCopy(_Out_writes_bytes_to_(Size, return) PCHAR Out, _In_ ULONG Size);
