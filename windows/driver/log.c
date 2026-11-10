// SPDX-License-Identifier: Apache-2.0
// Ring log for the MousePods L2CAP driver.

#include "driver.h"

static CHAR g_Mpl2Log[MPL2_LOG_SIZE];
static ULONG g_Mpl2LogWrite;    // next write position
static ULONG g_Mpl2LogUsed;     // bytes in the ring, at most MPL2_LOG_SIZE
static KSPIN_LOCK g_Mpl2LogLock;

_Use_decl_annotations_
VOID
Mpl2LogInit(VOID)
{
    KeInitializeSpinLock(&g_Mpl2LogLock);
    g_Mpl2LogWrite = 0;
    g_Mpl2LogUsed = 0;
}

_Use_decl_annotations_
VOID
Mpl2Log(PCSTR Format, ...)
{
    CHAR line[256];
    size_t remaining = 0;
    PSTR end = NULL;
    size_t len;
    size_t i;
    va_list args;
    ULONGLONG ms;
    KIRQL irql;
    NTSTATUS status;

    ms = KeQueryInterruptTime() / 10000;   // 100 ns units since boot

    status = RtlStringCbPrintfExA(line, sizeof(line) - 1, &end, &remaining, 0,
                                  "%10I64u.%03u ", ms / 1000, (ULONG)(ms % 1000));
    if (!NT_SUCCESS(status) || end == NULL) {
        return;
    }

    va_start(args, Format);
    // A line that does not fit is cut; the ring never gets partial bytes of
    // a multi-byte sequence because all text is ASCII.
    (void)RtlStringCbVPrintfExA(end, remaining, &end, &remaining, 0, Format, args);
    va_end(args);

    if (end == NULL) {
        return;
    }
    len = (size_t)(end - line);
    if (len >= sizeof(line) - 1) {
        len = sizeof(line) - 2;
    }
    line[len] = '\n';
    len++;

    KeAcquireSpinLock(&g_Mpl2LogLock, &irql);
    for (i = 0; i < len; i++) {
        g_Mpl2Log[g_Mpl2LogWrite] = line[i];
        g_Mpl2LogWrite = (g_Mpl2LogWrite + 1) % MPL2_LOG_SIZE;
    }
    g_Mpl2LogUsed = min(g_Mpl2LogUsed + (ULONG)len, (ULONG)MPL2_LOG_SIZE);
    KeReleaseSpinLock(&g_Mpl2LogLock, irql);
}

_Use_decl_annotations_
ULONG
Mpl2LogCopy(PCHAR Out, ULONG Size)
{
    KIRQL irql;
    ULONG count;
    ULONG start;
    ULONG i;

    KeAcquireSpinLock(&g_Mpl2LogLock, &irql);
    count = min(g_Mpl2LogUsed, Size);
    // The newest `count` bytes end at the write position.
    start = (g_Mpl2LogWrite + MPL2_LOG_SIZE - count) % MPL2_LOG_SIZE;
    for (i = 0; i < count; i++) {
        Out[i] = g_Mpl2Log[(start + i) % MPL2_LOG_SIZE];
    }
    KeReleaseSpinLock(&g_Mpl2LogLock, irql);
    return count;
}
