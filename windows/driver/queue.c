// SPDX-License-Identifier: Apache-2.0
// MousePods L2CAP driver: IOCTL, read and write dispatch, handle cleanup.
//
// Queue layout follows Microsoft's Windows-driver-samples bluetooth/bthecho
// (MIT licence, Copyright (c) Microsoft Corporation).

#include "driver.h"

_Use_decl_annotations_
NTSTATUS
Mpl2QueuesCreate(PMPL2_DEVICE Dev)
{
    NTSTATUS status;
    WDF_IO_QUEUE_CONFIG config;

    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(&config, WdfIoQueueDispatchParallel);
    config.EvtIoDeviceControl = Mpl2EvtIoDeviceControl;
    config.EvtIoRead = Mpl2EvtIoRead;
    config.EvtIoWrite = Mpl2EvtIoWrite;
    config.PowerManaged = WdfFalse;
    status = WdfIoQueueCreate(Dev->Device, &config, WDF_NO_OBJECT_ATTRIBUTES, WDF_NO_HANDLE);
    if (!NT_SUCCESS(status)) {
        Mpl2Log("creating the default queue failed 0x%08X", (ULONG)status);
        return status;
    }

    // Reads wait here until a packet arrives.
    WDF_IO_QUEUE_CONFIG_INIT(&config, WdfIoQueueDispatchManual);
    config.PowerManaged = WdfFalse;
    status = WdfIoQueueCreate(Dev->Device, &config, WDF_NO_OBJECT_ATTRIBUTES, &Dev->ReadQueue);
    if (!NT_SUCCESS(status)) {
        Mpl2Log("creating the read queue failed 0x%08X", (ULONG)status);
    }
    return status;
}

_IRQL_requires_max_(DISPATCH_LEVEL)
static VOID
Mpl2Info(_In_ PMPL2_DEVICE Dev, _In_ WDFREQUEST Request)
{
    NTSTATUS status;
    MPL2_INFO *info = NULL;

    status = WdfRequestRetrieveOutputBuffer(Request, sizeof(MPL2_INFO), (PVOID *)&info, NULL);
    if (!NT_SUCCESS(status) || info == NULL) {
        Mpl2Log("INFO: output buffer too small 0x%08X", (ULONG)status);
        WdfRequestComplete(Request, STATUS_BUFFER_TOO_SMALL);
        return;
    }
    RtlZeroMemory(info, sizeof(*info));
    info->version = MPL2_VERSION;
    WdfSpinLockAcquire(Dev->Lock);
    info->route = Dev->HaveInterface ? Dev->Route : MPL2_ROUTE_NONE;
    info->radio_addr = Dev->RadioAddr;
    WdfSpinLockRelease(Dev->Lock);
    WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS, sizeof(MPL2_INFO));
}

_IRQL_requires_max_(DISPATCH_LEVEL)
static VOID
Mpl2LogIoctl(_In_ WDFREQUEST Request)
{
    NTSTATUS status;
    PCHAR out = NULL;
    size_t size = 0;
    ULONG copied;

    status = WdfRequestRetrieveOutputBuffer(Request, 1, (PVOID *)&out, &size);
    if (!NT_SUCCESS(status) || out == NULL) {
        Mpl2Log("LOG: no output buffer 0x%08X", (ULONG)status);
        WdfRequestComplete(Request, STATUS_BUFFER_TOO_SMALL);
        return;
    }
    if (size > MPL2_LOG_SIZE) {
        size = MPL2_LOG_SIZE;
    }
    copied = Mpl2LogCopy(out, (ULONG)size);
    WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS, copied);
}

_Use_decl_annotations_
VOID
Mpl2EvtIoDeviceControl(WDFQUEUE Queue, WDFREQUEST Request, size_t OutputBufferLength,
                       size_t InputBufferLength, ULONG IoControlCode)
{
    PMPL2_DEVICE dev = Mpl2GetDevice(WdfIoQueueGetDevice(Queue));
    WDFFILEOBJECT file = WdfRequestGetFileObject(Request);

    UNREFERENCED_PARAMETER(OutputBufferLength);
    UNREFERENCED_PARAMETER(InputBufferLength);

    switch (IoControlCode) {
    case IOCTL_MPL2_INFO:
        Mpl2Info(dev, Request);
        break;
    case IOCTL_MPL2_CONNECT:
        if (file == NULL) {
            WdfRequestComplete(Request, STATUS_INVALID_DEVICE_REQUEST);
            break;
        }
        Mpl2Connect(dev, Request, file);
        break;
    case IOCTL_MPL2_DISCONNECT:
        if (file == NULL) {
            WdfRequestComplete(Request, STATUS_INVALID_DEVICE_REQUEST);
            break;
        }
        Mpl2Disconnect(dev, Request, file);
        break;
    case IOCTL_MPL2_LOG:
        Mpl2LogIoctl(Request);
        break;
    default:
        Mpl2Log("unknown IOCTL 0x%08X", IoControlCode);
        WdfRequestComplete(Request, STATUS_INVALID_DEVICE_REQUEST);
        break;
    }
}

_Use_decl_annotations_
VOID
Mpl2EvtIoRead(WDFQUEUE Queue, WDFREQUEST Request, size_t Length)
{
    PMPL2_DEVICE dev = Mpl2GetDevice(WdfIoQueueGetDevice(Queue));
    WDFFILEOBJECT file = WdfRequestGetFileObject(Request);

    UNREFERENCED_PARAMETER(Length);

    if (file == NULL) {
        WdfRequestComplete(Request, STATUS_INVALID_DEVICE_REQUEST);
        return;
    }
    Mpl2Read(dev, Request, file);
}

_Use_decl_annotations_
VOID
Mpl2EvtIoWrite(WDFQUEUE Queue, WDFREQUEST Request, size_t Length)
{
    PMPL2_DEVICE dev = Mpl2GetDevice(WdfIoQueueGetDevice(Queue));
    WDFFILEOBJECT file = WdfRequestGetFileObject(Request);

    if (file == NULL) {
        WdfRequestComplete(Request, STATUS_INVALID_DEVICE_REQUEST);
        return;
    }
    Mpl2Write(dev, Request, file, Length);
}

// The last handle of a file object is closed (also when the process dies):
// if it owns the channel, close the channel.
_Use_decl_annotations_
VOID
Mpl2EvtFileCleanup(WDFFILEOBJECT FileObject)
{
    PMPL2_DEVICE dev = Mpl2GetDevice(WdfFileObjectGetDevice(FileObject));

    Mpl2CloseAndWait(dev, FileObject, "owning handle closed");
}
