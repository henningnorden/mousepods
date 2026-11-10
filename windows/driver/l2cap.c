// SPDX-License-Identifier: Apache-2.0
// MousePods L2CAP driver: the channel (open, close, continuous reader, send).
//
// BRB submission, the continuous reader and the close sequence are based on
// Microsoft's Windows-driver-samples bluetooth/bthecho (MIT licence,
// Copyright (c) Microsoft Corporation), reduced to one outgoing channel.
//
// One channel per device. State changes happen under Dev->Lock:
//   Idle -> Connecting -> Open -> Closing -> Idle
// Closing ends when both the close BRB and the reader have finished
// (TeardownPending counts them).

#include "driver.h"

static EVT_WDF_REQUEST_COMPLETION_ROUTINE Mpl2ConnectComplete;
static EVT_WDF_REQUEST_COMPLETION_ROUTINE Mpl2CloseComplete;
static EVT_WDF_REQUEST_COMPLETION_ROUTINE Mpl2ReadComplete;
static EVT_WDF_REQUEST_COMPLETION_ROUTINE Mpl2WriteComplete;

_IRQL_requires_max_(DISPATCH_LEVEL)
static VOID
Mpl2IndicationCallback(_In_opt_ PVOID Context, _In_ INDICATION_CODE Indication,
                       _In_ PINDICATION_PARAMETERS Parameters);

_IRQL_requires_max_(DISPATCH_LEVEL)
static VOID
Mpl2ReaderSubmit(_In_ PMPL2_DEVICE Dev);

_IRQL_requires_max_(DISPATCH_LEVEL)
static VOID
Mpl2TeardownStep(_In_ PMPL2_DEVICE Dev);

_IRQL_requires_max_(DISPATCH_LEVEL)
static NTSTATUS
Mpl2FormatBrb(_In_ PMPL2_DEVICE Dev, _In_ WDFREQUEST Request, _In_ WDFMEMORY BrbMemory)
{
    return WdfIoTargetFormatRequestForInternalIoctlOthers(Dev->Target, Request,
                                                          IOCTL_INTERNAL_BTH_SUBMIT_BRB,
                                                          BrbMemory, NULL, NULL, NULL,
                                                          NULL, NULL);
}

_IRQL_requires_max_(DISPATCH_LEVEL)
static VOID
Mpl2ReuseRequest(_In_ WDFREQUEST Request)
{
    WDF_REQUEST_REUSE_PARAMS params;
    NTSTATUS status;

    WDF_REQUEST_REUSE_PARAMS_INIT(&params, WDF_REQUEST_REUSE_NO_FLAGS, STATUS_SUCCESS);
    status = WdfRequestReuse(Request, &params);
    if (!NT_SUCCESS(status)) {
        Mpl2Log("WdfRequestReuse failed 0x%08X", (ULONG)status);
    }
}

_Use_decl_annotations_
NTSTATUS
Mpl2BrbRequestsCreate(PMPL2_DEVICE Dev)
{
    NTSTATUS status;
    WDF_OBJECT_ATTRIBUTES attributes;

    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ParentObject = Dev->Device;

    status = WdfRequestCreate(&attributes, Dev->Target, &Dev->ConnectReq);
    if (NT_SUCCESS(status)) {
        status = WdfRequestCreate(&attributes, Dev->Target, &Dev->CloseReq);
    }
    if (NT_SUCCESS(status)) {
        status = WdfRequestCreate(&attributes, Dev->Target, &Dev->ReadReq);
    }
    if (NT_SUCCESS(status)) {
        status = WdfMemoryCreatePreallocated(&attributes, &Dev->OpenBrb, sizeof(Dev->OpenBrb),
                                             &Dev->ConnectBrbMem);
    }
    if (NT_SUCCESS(status)) {
        status = WdfMemoryCreatePreallocated(&attributes, &Dev->CloseBrb, sizeof(Dev->CloseBrb),
                                             &Dev->CloseBrbMem);
    }
    if (NT_SUCCESS(status)) {
        status = WdfMemoryCreatePreallocated(&attributes, &Dev->ReadBrb, sizeof(Dev->ReadBrb),
                                             &Dev->ReadBrbMem);
    }
    if (!NT_SUCCESS(status)) {
        return status;
    }

    Dev->Bth.BthInitializeBrb((PBRB)&Dev->OpenBrb, BRB_L2CA_OPEN_CHANNEL);
    Dev->Bth.BthInitializeBrb((PBRB)&Dev->CloseBrb, BRB_L2CA_CLOSE_CHANNEL);
    Dev->Bth.BthInitializeBrb((PBRB)&Dev->ReadBrb, BRB_L2CA_ACL_TRANSFER);
    Dev->RequestsReady = TRUE;
    return STATUS_SUCCESS;
}

_Use_decl_annotations_
NTSTATUS
Mpl2ReadRadioAddress(PMPL2_DEVICE Dev)
{
    struct _BRB_GET_LOCAL_BD_ADDR brb;
    WDF_MEMORY_DESCRIPTOR descriptor;
    WDF_REQUEST_SEND_OPTIONS options;
    NTSTATUS status;

    RtlZeroMemory(&brb, sizeof(brb));
    Dev->Bth.BthInitializeBrb((PBRB)&brb, BRB_HCI_GET_LOCAL_BD_ADDR);
    WDF_MEMORY_DESCRIPTOR_INIT_BUFFER(&descriptor, &brb, sizeof(brb));
    WDF_REQUEST_SEND_OPTIONS_INIT(&options, 0);
    WDF_REQUEST_SEND_OPTIONS_SET_TIMEOUT(&options, WDF_REL_TIMEOUT_IN_MS(3000));

    status = WdfIoTargetSendInternalIoctlOthersSynchronously(Dev->Target, NULL,
                                                             IOCTL_INTERNAL_BTH_SUBMIT_BRB,
                                                             &descriptor, NULL, NULL,
                                                             &options, NULL);
    if (NT_SUCCESS(status)) {
        WdfSpinLockAcquire(Dev->Lock);
        Dev->RadioAddr = brb.BtAddress;
        WdfSpinLockRelease(Dev->Lock);
        Mpl2Log("local radio %012I64X", brb.BtAddress);
    } else {
        Mpl2Log("reading the local radio address failed 0x%08X bt 0x%02X",
                (ULONG)status, (ULONG)brb.Hdr.BtStatus);
    }
    return status;
}

// Which step of an outgoing open failed. The stack reports one status for the
// whole open, so this is a best guess from the status, the HCI status and
// whether a channel handle was assigned (the remote accepted the connect, so
// a later failure is in configuration). The raw values go to user mode too.
_IRQL_requires_max_(DISPATCH_LEVEL)
static ULONG
Mpl2StageFor(_In_ NTSTATUS Status, _In_ BTHSTATUS BtStatus, _In_ BOOLEAN HaveHandle)
{
    switch (Status) {
    case STATUS_INVALID_PARAMETER:
    case STATUS_INVALID_DEVICE_REQUEST:
    case STATUS_INVALID_DEVICE_STATE:
    case STATUS_NOT_SUPPORTED:
    case STATUS_NOT_IMPLEMENTED:
    case STATUS_INSUFFICIENT_RESOURCES:
    case STATUS_INVALID_BUFFER_SIZE:
    case STATUS_DEVICE_NOT_READY:
        return MPL2_STAGE_BRB;
    case STATUS_REQUEST_NOT_ACCEPTED:
    case STATUS_CONNECTION_REFUSED:
    case STATUS_REMOTE_NOT_LISTENING:
    case STATUS_IO_TIMEOUT:
    case STATUS_CANCELLED:
    case STATUS_DEVICE_NOT_CONNECTED:
    case STATUS_CONNECTION_ABORTED:
    case STATUS_CONNECTION_RESET:
    case STATUS_LINK_FAILED:
    case STATUS_LINK_TIMEOUT:
    case STATUS_ACCESS_DENIED:
        return HaveHandle ? MPL2_STAGE_CONFIG : MPL2_STAGE_REMOTE;
    default:
        break;
    }
    if (HaveHandle) {
        return MPL2_STAGE_CONFIG;
    }
    return BtStatus != BTH_ERROR_SUCCESS ? MPL2_STAGE_REMOTE : MPL2_STAGE_BRB;
}

// Completes a CONNECT that reached the stack: the IOCTL succeeds and the
// result is in MPL2_CONNECT_OUT.
_IRQL_requires_max_(DISPATCH_LEVEL)
static VOID
Mpl2CompleteConnect(_In_opt_ WDFREQUEST Request, _In_ NTSTATUS Result, _In_ ULONG BtStatus,
                    _In_ ULONG Stage, _In_ USHORT OutMtu)
{
    NTSTATUS status;
    MPL2_CONNECT_OUT *out = NULL;

    if (Request == NULL) {
        return;
    }
    status = WdfRequestRetrieveOutputBuffer(Request, sizeof(MPL2_CONNECT_OUT), (PVOID *)&out, NULL);
    if (!NT_SUCCESS(status) || out == NULL) {
        WdfRequestComplete(Request, NT_SUCCESS(Result) ? STATUS_BUFFER_TOO_SMALL : Result);
        return;
    }
    RtlZeroMemory(out, sizeof(*out));
    out->ntstatus = (INT32)Result;
    out->bt_status = BtStatus;
    out->stage = Stage;
    out->out_mtu = OutMtu;
    WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS, sizeof(MPL2_CONNECT_OUT));
}

// A connect attempt failed: back to Idle and report.
_IRQL_requires_max_(DISPATCH_LEVEL)
static VOID
Mpl2ConnectFailed(_In_ PMPL2_DEVICE Dev, _In_ NTSTATUS Result, _In_ ULONG BtStatus,
                  _In_ ULONG Stage)
{
    WDFREQUEST user;
    WDFREQUEST disconnect;
    WDFMEMORY rx;
    WDFMEMORY ring;

    WdfSpinLockAcquire(Dev->Lock);
    user = Dev->UserConnect;
    Dev->UserConnect = NULL;
    disconnect = Dev->UserDisconnect;
    Dev->UserDisconnect = NULL;
    rx = Dev->RxMem;
    ring = Dev->RingMem;
    Dev->RxMem = NULL;
    Dev->RxData = NULL;
    Dev->RingMem = NULL;
    Dev->Ring = NULL;
    Dev->Owner = NULL;
    Dev->AbortConnect = FALSE;
    Dev->State = Mpl2Idle;
    WdfSpinLockRelease(Dev->Lock);

    if (rx != NULL) {
        WdfObjectDelete(rx);
    }
    if (ring != NULL) {
        WdfObjectDelete(ring);
    }
    KeSetEvent(&Dev->IdleEvent, IO_NO_INCREMENT, FALSE);
    Mpl2Log("state connecting -> idle: connect failed 0x%08X bt 0x%X stage %u",
            (ULONG)Result, BtStatus, Stage);

    Mpl2CompleteConnect(user, Result, BtStatus, Stage, 0);
    if (disconnect != NULL) {
        WdfRequestComplete(disconnect, STATUS_SUCCESS);
    }
}

_IRQL_requires_max_(DISPATCH_LEVEL)
static NTSTATUS
Mpl2AllocBuffers(_In_ PMPL2_DEVICE Dev, _In_ USHORT Mtu)
{
    NTSTATUS status;
    WDF_OBJECT_ATTRIBUTES attributes;
    WDFMEMORY rx = NULL;
    WDFMEMORY ring = NULL;
    PVOID rxData = NULL;
    PVOID ringData = NULL;

    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ParentObject = Dev->Device;
    status = WdfMemoryCreate(&attributes, NonPagedPoolNx, MPL2_POOLTAG, Mtu, &rx, &rxData);
    if (!NT_SUCCESS(status)) {
        return status;
    }
    status = WdfMemoryCreate(&attributes, NonPagedPoolNx, MPL2_POOLTAG,
                             (size_t)Mtu * MPL2_RX_SLOTS, &ring, &ringData);
    if (!NT_SUCCESS(status)) {
        WdfObjectDelete(rx);
        return status;
    }

    WdfSpinLockAcquire(Dev->Lock);
    Dev->RxMem = rx;
    Dev->RxData = (PUCHAR)rxData;
    Dev->RingMem = ring;
    Dev->Ring = (PUCHAR)ringData;
    WdfSpinLockRelease(Dev->Lock);
    return STATUS_SUCCESS;
}

_Use_decl_annotations_
VOID
Mpl2Connect(PMPL2_DEVICE Dev, WDFREQUEST Request, WDFFILEOBJECT File)
{
    NTSTATUS status;
    MPL2_CONNECT_IN *in = NULL;
    MPL2_CONNECT_OUT *out = NULL;
    MPL2_CONNECT_IN args;
    ULONG channelFlags;
    ULONG timeoutMs;
    BOOLEAN ready;
    NTSTATUS lookup;
    struct _BRB_L2CA_OPEN_CHANNEL *brb = &Dev->OpenBrb;
    WDF_REQUEST_SEND_OPTIONS options;

    status = WdfRequestRetrieveInputBuffer(Request, sizeof(MPL2_CONNECT_IN), (PVOID *)&in, NULL);
    if (!NT_SUCCESS(status) || in == NULL) {
        Mpl2Log("CONNECT refused: input buffer too small");
        WdfRequestComplete(Request, STATUS_INVALID_PARAMETER);
        return;
    }
    status = WdfRequestRetrieveOutputBuffer(Request, sizeof(MPL2_CONNECT_OUT), (PVOID *)&out, NULL);
    if (!NT_SUCCESS(status) || out == NULL) {
        Mpl2Log("CONNECT refused: output buffer too small");
        WdfRequestComplete(Request, STATUS_BUFFER_TOO_SMALL);
        return;
    }
    // METHOD_BUFFERED: input and output share one buffer.
    args = *in;

    // Policy first: only the AAP channel, before anything is sent.
    if (args.psm != MPL2_PSM_AAP) {
        Mpl2Log("CONNECT refused: PSM 0x%04X is not allowed", (ULONG)args.psm);
        WdfRequestComplete(Request, STATUS_ACCESS_DENIED);
        return;
    }
    if (args.security > MPL2_SEC_AUTH_ENCRYPT || args.mtu < L2CAP_MIN_MTU ||
        args.bth_addr == 0 || args.bth_addr > 0xFFFFFFFFFFFFULL) {
        Mpl2Log("CONNECT refused: bad arguments (addr %012I64X mtu %u security %u)",
                args.bth_addr, (ULONG)args.mtu, args.security);
        WdfRequestComplete(Request, STATUS_INVALID_PARAMETER);
        return;
    }
    timeoutMs = args.timeout_ms != 0 ? args.timeout_ms : MPL2_DEFAULT_CONNECT_TIMEOUT_MS;

    WdfSpinLockAcquire(Dev->Lock);
    if (Dev->State != Mpl2Idle) {
        WdfSpinLockRelease(Dev->Lock);
        Mpl2Log("CONNECT refused: a channel is already open or in progress");
        WdfRequestComplete(Request, STATUS_DEVICE_BUSY);
        return;
    }
    ready = Dev->HaveInterface && Dev->RequestsReady;
    lookup = Dev->LookupStatus;
    if (ready) {
        Dev->State = Mpl2Connecting;
        Dev->Owner = File;
        Dev->AbortConnect = FALSE;
        Dev->UserConnect = Request;
        Dev->Remote = args.bth_addr;
        Dev->Channel = NULL;
        Dev->BufMtu = args.mtu;
        Dev->OutMtu = 0;
        Dev->InMtu = 0;
        Dev->RingHead = 0;
        Dev->RingCount = 0;
        Dev->RxPackets = 0;
        Dev->TxPackets = 0;
        Dev->Dropped = 0;
        Dev->Oversize = 0;
        Dev->ReaderStop = FALSE;
        KeClearEvent(&Dev->IdleEvent);
    }
    WdfSpinLockRelease(Dev->Lock);

    if (!ready) {
        if (NT_SUCCESS(lookup)) {
            lookup = STATUS_DEVICE_NOT_CONNECTED;
        }
        Mpl2Log("CONNECT failed: no route to the Bluetooth stack 0x%08X", (ULONG)lookup);
        Mpl2CompleteConnect(Request, lookup, 0, MPL2_STAGE_LOOKUP, 0);
        return;
    }

    Mpl2Log("state idle -> connecting: %012I64X psm 0x%04X mtu %u security %u timeout %u ms",
            args.bth_addr, (ULONG)args.psm, (ULONG)args.mtu, args.security, timeoutMs);

    status = Mpl2AllocBuffers(Dev, args.mtu);
    if (!NT_SUCCESS(status)) {
        Mpl2ConnectFailed(Dev, status, 0, MPL2_STAGE_BRB);
        return;
    }

    switch (args.security) {
    case MPL2_SEC_AUTH:
        channelFlags = CF_LINK_AUTHENTICATED;
        break;
    case MPL2_SEC_AUTH_ENCRYPT:
        channelFlags = CF_LINK_AUTHENTICATED | CF_LINK_ENCRYPTED;
        break;
    default:
        channelFlags = 0;
        break;
    }

    Dev->Bth.BthReuseBrb((PBRB)brb, BRB_L2CA_OPEN_CHANNEL);
    brb->ChannelHandle = NULL;
    brb->BtAddress = args.bth_addr;
    brb->Psm = MPL2_PSM_AAP;
    brb->ChannelFlags = CF_ROLE_EITHER | channelFlags;
    brb->ConfigOut.Flags = CFG_MTU;
    brb->ConfigOut.Mtu.Min = L2CAP_MIN_MTU;
    brb->ConfigOut.Mtu.Preferred = args.mtu;
    brb->ConfigOut.Mtu.Max = args.mtu;
    brb->ConfigIn.Flags = CFG_MTU;
    brb->ConfigIn.Mtu.Min = L2CAP_MIN_MTU;
    brb->ConfigIn.Mtu.Preferred = args.mtu;
    brb->ConfigIn.Mtu.Max = args.mtu;
    brb->CallbackFlags = CALLBACK_DISCONNECT;
    brb->Callback = Mpl2IndicationCallback;
    brb->CallbackContext = Dev;
    brb->ReferenceObject = (PVOID)WdfDeviceWdmGetDeviceObject(Dev->Device);
    brb->IncomingQueueDepth = 50;

    Mpl2ReuseRequest(Dev->ConnectReq);
    status = Mpl2FormatBrb(Dev, Dev->ConnectReq, Dev->ConnectBrbMem);
    if (!NT_SUCCESS(status)) {
        Mpl2ConnectFailed(Dev, status, 0, MPL2_STAGE_BRB);
        return;
    }
    WdfRequestSetCompletionRoutine(Dev->ConnectReq, Mpl2ConnectComplete, Dev);
    WDF_REQUEST_SEND_OPTIONS_INIT(&options, 0);
    WDF_REQUEST_SEND_OPTIONS_SET_TIMEOUT(&options, WDF_REL_TIMEOUT_IN_MS(timeoutMs));
    if (!WdfRequestSend(Dev->ConnectReq, Dev->Target, &options)) {
        status = WdfRequestGetStatus(Dev->ConnectReq);
        Mpl2ConnectFailed(Dev, status, 0, MPL2_STAGE_BRB);
    }
}

_Use_decl_annotations_
static VOID
Mpl2ConnectComplete(WDFREQUEST Request, WDFIOTARGET Target, PWDF_REQUEST_COMPLETION_PARAMS Params,
                    WDFCONTEXT Context)
{
    PMPL2_DEVICE dev = (PMPL2_DEVICE)Context;
    struct _BRB_L2CA_OPEN_CHANNEL *brb = &dev->OpenBrb;
    NTSTATUS irpStatus = Params->IoStatus.Status;
    NTSTATUS result;
    ULONG bt;
    WDFREQUEST user;
    BOOLEAN abort;
    USHORT outMtu;
    USHORT inMtu;

    UNREFERENCED_PARAMETER(Request);
    UNREFERENCED_PARAMETER(Target);

    result = !NT_SUCCESS(brb->Hdr.Status) ? brb->Hdr.Status : irpStatus;
    bt = brb->Hdr.BtStatus;
    if (result == STATUS_REQUEST_NOT_ACCEPTED) {
        // The remote's L2CAP connect result.
        bt |= ((ULONG)brb->Response) << 16;
    }

    if (!NT_SUCCESS(result)) {
        Mpl2Log("open channel failed: irp 0x%08X brb 0x%08X bt 0x%02X response 0x%04X handle %s",
                (ULONG)irpStatus, (ULONG)brb->Hdr.Status, (ULONG)brb->Hdr.BtStatus,
                result == STATUS_REQUEST_NOT_ACCEPTED ? (ULONG)brb->Response : 0UL,
                brb->ChannelHandle != NULL ? "assigned" : "none");
        Mpl2ConnectFailed(dev, result, bt,
                          Mpl2StageFor(result, brb->Hdr.BtStatus, brb->ChannelHandle != NULL));
        return;
    }

    outMtu = brb->OutResults.Params.Mtu;
    inMtu = brb->InResults.Params.Mtu;
    if (outMtu == 0) {
        outMtu = L2CAP_DEFAULT_MTU;
    }

    WdfSpinLockAcquire(dev->Lock);
    user = dev->UserConnect;
    dev->UserConnect = NULL;
    dev->Channel = brb->ChannelHandle;
    dev->OutMtu = outMtu;
    dev->InMtu = inMtu;
    dev->State = Mpl2Open;
    abort = dev->AbortConnect;
    dev->AbortConnect = FALSE;
    WdfSpinLockRelease(dev->Lock);

    Mpl2Log("state connecting -> open: out mtu %u in mtu %u", (ULONG)outMtu, (ULONG)inMtu);
    if (inMtu > dev->BufMtu) {
        Mpl2Log("remote may send %u bytes, receive buffer is %u", (ULONG)inMtu,
                (ULONG)dev->BufMtu);
    }

    Mpl2CompleteConnect(user, STATUS_SUCCESS, bt, MPL2_STAGE_OPEN, outMtu);

    if (abort) {
        (void)Mpl2BeginTeardown(dev, "handle closed or disconnect while connecting");
    } else {
        Mpl2ReaderSubmit(dev);
    }
}

// Bluetooth stack notifications for the open channel.
_Use_decl_annotations_
static VOID
Mpl2IndicationCallback(PVOID Context, INDICATION_CODE Indication, PINDICATION_PARAMETERS Parameters)
{
    PMPL2_DEVICE dev = (PMPL2_DEVICE)Context;

    if (dev == NULL) {
        return;
    }
    switch (Indication) {
    case IndicationAddReference:
    case IndicationReleaseReference:
        break;
    case IndicationRemoteDisconnect:
        Mpl2Log("remote disconnect indication, reason %u",
                Parameters != NULL ? (ULONG)Parameters->Parameters.Disconnect.Reason : 0xFFUL);
        (void)Mpl2BeginTeardown(dev, "remote disconnect");
        break;
    default:
        Mpl2Log("indication %u ignored", (ULONG)Indication);
        break;
    }
}

// --------------------------------------------------------------------------
// Close

_IRQL_requires_max_(DISPATCH_LEVEL)
static VOID
Mpl2FailPendingReads(_In_ PMPL2_DEVICE Dev)
{
    WDFREQUEST request;
    ULONG count = 0;

    while (NT_SUCCESS(WdfIoQueueRetrieveNextRequest(Dev->ReadQueue, &request))) {
        WdfRequestComplete(request, STATUS_DEVICE_NOT_CONNECTED);
        count++;
    }
    if (count != 0) {
        Mpl2Log("%u pending reads completed with STATUS_DEVICE_NOT_CONNECTED", count);
    }
}

_Use_decl_annotations_
BOOLEAN
Mpl2BeginTeardown(PMPL2_DEVICE Dev, PCSTR Reason)
{
    BOOLEAN readerActive;
    L2CAP_CHANNEL_HANDLE channel;
    BTH_ADDR remote;
    ULONG rx, tx, dropped, oversize, queued;
    NTSTATUS status;

    WdfSpinLockAcquire(Dev->Lock);
    if (Dev->State != Mpl2Open) {
        WdfSpinLockRelease(Dev->Lock);
        return FALSE;
    }
    Dev->State = Mpl2Closing;
    Dev->ReaderStop = TRUE;
    readerActive = Dev->ReaderRunning;
    Dev->TeardownPending = readerActive ? 2 : 1;
    queued = Dev->RingCount;
    Dev->RingCount = 0;
    Dev->RingHead = 0;
    channel = Dev->Channel;
    remote = Dev->Remote;
    rx = Dev->RxPackets;
    tx = Dev->TxPackets;
    dropped = Dev->Dropped;
    oversize = Dev->Oversize;
    WdfSpinLockRelease(Dev->Lock);

    Mpl2Log("state open -> closing (%s): rx %u tx %u dropped %u oversize %u unread %u",
            Reason, rx, tx, dropped, oversize, queued);

    Mpl2FailPendingReads(Dev);

    if (readerActive) {
        (void)WdfRequestCancelSentRequest(Dev->ReadReq);
    }

    Mpl2ReuseRequest(Dev->CloseReq);
    Dev->Bth.BthReuseBrb((PBRB)&Dev->CloseBrb, BRB_L2CA_CLOSE_CHANNEL);
    Dev->CloseBrb.BtAddress = remote;
    Dev->CloseBrb.ChannelHandle = channel;
    status = Mpl2FormatBrb(Dev, Dev->CloseReq, Dev->CloseBrbMem);
    if (NT_SUCCESS(status)) {
        WdfRequestSetCompletionRoutine(Dev->CloseReq, Mpl2CloseComplete, Dev);
        if (WdfRequestSend(Dev->CloseReq, Dev->Target, WDF_NO_SEND_OPTIONS)) {
            return TRUE;
        }
        status = WdfRequestGetStatus(Dev->CloseReq);
    }
    Mpl2Log("sending close channel failed 0x%08X", (ULONG)status);
    Mpl2TeardownStep(Dev);
    return TRUE;
}

_Use_decl_annotations_
static VOID
Mpl2CloseComplete(WDFREQUEST Request, WDFIOTARGET Target, PWDF_REQUEST_COMPLETION_PARAMS Params,
                  WDFCONTEXT Context)
{
    PMPL2_DEVICE dev = (PMPL2_DEVICE)Context;
    NTSTATUS status = Params->IoStatus.Status;

    UNREFERENCED_PARAMETER(Request);
    UNREFERENCED_PARAMETER(Target);

    if (!NT_SUCCESS(status)) {
        // Expected after a remote disconnect; the channel is gone either way.
        Mpl2Log("close channel completed 0x%08X bt 0x%02X", (ULONG)status,
                (ULONG)dev->CloseBrb.Hdr.BtStatus);
    }
    Mpl2TeardownStep(dev);
}

_Use_decl_annotations_
static VOID
Mpl2TeardownStep(PMPL2_DEVICE Dev)
{
    LONG remaining;
    WDFREQUEST disconnect = NULL;
    WDFMEMORY rx = NULL;
    WDFMEMORY ring = NULL;

    WdfSpinLockAcquire(Dev->Lock);
    remaining = --Dev->TeardownPending;
    if (remaining == 0) {
        Dev->State = Mpl2Idle;
        Dev->Owner = NULL;
        Dev->Channel = NULL;
        Dev->ReaderStop = FALSE;
        disconnect = Dev->UserDisconnect;
        Dev->UserDisconnect = NULL;
        rx = Dev->RxMem;
        ring = Dev->RingMem;
        Dev->RxMem = NULL;
        Dev->RxData = NULL;
        Dev->RingMem = NULL;
        Dev->Ring = NULL;
    }
    WdfSpinLockRelease(Dev->Lock);

    if (remaining != 0) {
        return;
    }
    if (rx != NULL) {
        WdfObjectDelete(rx);
    }
    if (ring != NULL) {
        WdfObjectDelete(ring);
    }
    KeSetEvent(&Dev->IdleEvent, IO_NO_INCREMENT, FALSE);
    Mpl2Log("state closing -> idle");
    if (disconnect != NULL) {
        WdfRequestComplete(disconnect, STATUS_SUCCESS);
    }
}

_Use_decl_annotations_
VOID
Mpl2Disconnect(PMPL2_DEVICE Dev, WDFREQUEST Request, WDFFILEOBJECT File)
{
    MPL2_CHANNEL_STATE state;
    BOOLEAN pend = FALSE;

    WdfSpinLockAcquire(Dev->Lock);
    state = Dev->State;
    if (state != Mpl2Idle && Dev->Owner == File && Dev->UserDisconnect == NULL) {
        // Completed once the channel is closed.
        Dev->UserDisconnect = Request;
        pend = TRUE;
        if (state == Mpl2Connecting) {
            Dev->AbortConnect = TRUE;
        }
    }
    WdfSpinLockRelease(Dev->Lock);

    if (!pend) {
        WdfRequestComplete(Request, STATUS_SUCCESS);
        return;
    }
    Mpl2Log("DISCONNECT requested in state %u", (ULONG)state);
    if (state == Mpl2Connecting) {
        (void)WdfRequestCancelSentRequest(Dev->ConnectReq);
    } else {
        (void)Mpl2BeginTeardown(Dev, "disconnect requested");
    }
}

_Use_decl_annotations_
VOID
Mpl2CloseAndWait(PMPL2_DEVICE Dev, WDFFILEOBJECT OnlyOwner, PCSTR Reason)
{
    MPL2_CHANNEL_STATE state;
    LARGE_INTEGER timeout;
    NTSTATUS status;

    WdfSpinLockAcquire(Dev->Lock);
    state = Dev->State;
    if (OnlyOwner != NULL && Dev->Owner != OnlyOwner) {
        state = Mpl2Idle;
    } else if (state == Mpl2Connecting) {
        Dev->AbortConnect = TRUE;
    }
    WdfSpinLockRelease(Dev->Lock);

    if (state == Mpl2Idle) {
        return;
    }
    if (state == Mpl2Connecting) {
        Mpl2Log("aborting connect (%s)", Reason);
        (void)WdfRequestCancelSentRequest(Dev->ConnectReq);
    } else {
        (void)Mpl2BeginTeardown(Dev, Reason);
    }

    timeout.QuadPart = WDF_REL_TIMEOUT_IN_MS(MPL2_TEARDOWN_WAIT_MS);
    status = KeWaitForSingleObject(&Dev->IdleEvent, Executive, KernelMode, FALSE, &timeout);
    if (status == STATUS_TIMEOUT) {
        Mpl2Log("channel did not close within %u ms (%s)", (ULONG)MPL2_TEARDOWN_WAIT_MS, Reason);
    }
}

// --------------------------------------------------------------------------
// Receive

_Use_decl_annotations_
VOID
Mpl2ResubmitDpc(PKDPC Dpc, PVOID DeferredContext, PVOID SystemArgument1, PVOID SystemArgument2)
{
    UNREFERENCED_PARAMETER(Dpc);
    UNREFERENCED_PARAMETER(SystemArgument1);
    UNREFERENCED_PARAMETER(SystemArgument2);

    if (DeferredContext != NULL) {
        Mpl2ReaderSubmit((PMPL2_DEVICE)DeferredContext);
    }
}

// The reader ended without being asked to: the link is gone.
_IRQL_requires_max_(DISPATCH_LEVEL)
static VOID
Mpl2ReaderEnded(_In_ PMPL2_DEVICE Dev, _In_ NTSTATUS Status)
{
    BOOLEAN stopping;

    WdfSpinLockAcquire(Dev->Lock);
    Dev->ReaderRunning = FALSE;
    stopping = Dev->ReaderStop;
    WdfSpinLockRelease(Dev->Lock);

    if (stopping) {
        Mpl2TeardownStep(Dev);
        return;
    }
    Mpl2Log("receive failed 0x%08X bt 0x%02X", (ULONG)Status, (ULONG)Dev->ReadBrb.Hdr.BtStatus);
    (void)Mpl2BeginTeardown(Dev, "receive failed");
}

_Use_decl_annotations_
static VOID
Mpl2ReaderSubmit(PMPL2_DEVICE Dev)
{
    NTSTATUS status;
    L2CAP_CHANNEL_HANDLE channel;
    BTH_ADDR remote;
    PUCHAR buffer;
    USHORT size;
    struct _BRB_L2CA_ACL_TRANSFER *brb = &Dev->ReadBrb;

    WdfSpinLockAcquire(Dev->Lock);
    if (Dev->State != Mpl2Open || Dev->ReaderStop || Dev->RxData == NULL) {
        WdfSpinLockRelease(Dev->Lock);
        return;
    }
    Dev->ReaderRunning = TRUE;
    channel = Dev->Channel;
    remote = Dev->Remote;
    buffer = Dev->RxData;
    size = Dev->BufMtu;
    WdfSpinLockRelease(Dev->Lock);

    Mpl2ReuseRequest(Dev->ReadReq);
    Dev->Bth.BthReuseBrb((PBRB)brb, BRB_L2CA_ACL_TRANSFER);
    brb->BtAddress = remote;
    brb->ChannelHandle = channel;
    brb->TransferFlags = ACL_TRANSFER_DIRECTION_IN | ACL_SHORT_TRANSFER_OK;
    brb->Buffer = buffer;
    brb->BufferMDL = NULL;
    brb->BufferSize = size;
    brb->Timeout = 0;

    status = Mpl2FormatBrb(Dev, Dev->ReadReq, Dev->ReadBrbMem);
    if (NT_SUCCESS(status)) {
        WdfRequestSetCompletionRoutine(Dev->ReadReq, Mpl2ReadComplete, Dev);
        if (WdfRequestSend(Dev->ReadReq, Dev->Target, WDF_NO_SEND_OPTIONS)) {
            return;
        }
        status = WdfRequestGetStatus(Dev->ReadReq);
    }
    Mpl2ReaderEnded(Dev, status);
}

// Takes the oldest packet out of the queue and completes Request with it.
// Called with Dev->Lock held; returns the completion status and length.
_IRQL_requires_max_(DISPATCH_LEVEL)
static NTSTATUS
Mpl2PopLocked(_In_ PMPL2_DEVICE Dev, _In_ WDFREQUEST Request, _Out_ PULONG_PTR Information,
              _Out_ PULONG PacketLength)
{
    NTSTATUS status;
    ULONG slot = Dev->RingHead;
    ULONG length = Dev->RingLen[slot];
    PUCHAR source = Dev->Ring + (SIZE_T)slot * Dev->BufMtu;
    PVOID buffer = NULL;
    size_t capacity = 0;

    *Information = 0;
    *PacketLength = length;
    Dev->RingHead = (Dev->RingHead + 1) % MPL2_RX_SLOTS;
    Dev->RingCount--;

    status = WdfRequestRetrieveOutputBuffer(Request, 0, &buffer, &capacity);
    if (!NT_SUCCESS(status) || buffer == NULL) {
        capacity = 0;
    }
    if (capacity < length) {
        Dev->Oversize++;
        return STATUS_BUFFER_TOO_SMALL;
    }
    if (length != 0 && buffer != NULL) {
        RtlCopyMemory(buffer, source, length);
    }
    *Information = length;
    return STATUS_SUCCESS;
}

// Hands queued packets to waiting reads.
_IRQL_requires_max_(DISPATCH_LEVEL)
static VOID
Mpl2DeliverPending(_In_ PMPL2_DEVICE Dev)
{
    WDFREQUEST request;
    NTSTATUS status;
    ULONG_PTR information;
    ULONG length;
    ULONG oversize;

    for (;;) {
        WdfSpinLockAcquire(Dev->Lock);
        if (Dev->State != Mpl2Open || Dev->RingCount == 0 || Dev->Ring == NULL) {
            WdfSpinLockRelease(Dev->Lock);
            return;
        }
        if (!NT_SUCCESS(WdfIoQueueRetrieveNextRequest(Dev->ReadQueue, &request))) {
            WdfSpinLockRelease(Dev->Lock);
            return;
        }
        status = Mpl2PopLocked(Dev, request, &information, &length);
        oversize = Dev->Oversize;
        WdfSpinLockRelease(Dev->Lock);

        if (status == STATUS_BUFFER_TOO_SMALL) {
            Mpl2Log("packet of %u bytes bigger than the read buffer, dropped (%u so far)",
                    length, oversize);
        }
        WdfRequestCompleteWithInformation(request, status, information);
    }
}

_Use_decl_annotations_
static VOID
Mpl2ReadComplete(WDFREQUEST Request, WDFIOTARGET Target, PWDF_REQUEST_COMPLETION_PARAMS Params,
                 WDFCONTEXT Context)
{
    PMPL2_DEVICE dev = (PMPL2_DEVICE)Context;
    NTSTATUS status = Params->IoStatus.Status;
    ULONG length;
    ULONG slot;
    ULONG dropped = 0;
    BOOLEAN droppedNow = FALSE;
    BOOLEAN tooBig = FALSE;
    BOOLEAN stopping;

    UNREFERENCED_PARAMETER(Request);
    UNREFERENCED_PARAMETER(Target);

    if (!NT_SUCCESS(status) && status != STATUS_BUFFER_OVERFLOW &&
        status != STATUS_BUFFER_TOO_SMALL && status != STATUS_INVALID_BUFFER_SIZE) {
        Mpl2ReaderEnded(dev, status);
        return;
    }

    length = dev->ReadBrb.BufferSize;

    WdfSpinLockAcquire(dev->Lock);
    dev->ReaderRunning = FALSE;
    stopping = dev->ReaderStop;
    if (!stopping && dev->State == Mpl2Open && dev->Ring != NULL && dev->RxData != NULL) {
        if (!NT_SUCCESS(status) || length > dev->BufMtu) {
            // Bigger than the receive buffer: counted, never truncated.
            dev->Oversize++;
            tooBig = TRUE;
        } else {
            if (dev->RingCount == MPL2_RX_SLOTS) {
                dev->RingHead = (dev->RingHead + 1) % MPL2_RX_SLOTS;
                dev->RingCount--;
                dev->Dropped++;
                dropped = dev->Dropped;
                droppedNow = TRUE;
            }
            slot = (dev->RingHead + dev->RingCount) % MPL2_RX_SLOTS;
            if (length != 0) {
                RtlCopyMemory(dev->Ring + (SIZE_T)slot * dev->BufMtu, dev->RxData, length);
            }
            dev->RingLen[slot] = (USHORT)length;
            dev->RingCount++;
            dev->RxPackets++;
        }
    }
    WdfSpinLockRelease(dev->Lock);

    if (tooBig) {
        Mpl2Log("received packet does not fit the %u byte buffer (0x%08X), dropped",
                (ULONG)dev->BufMtu, (ULONG)status);
    }
    if (droppedNow && (dropped == 1 || dropped % 64 == 0)) {
        Mpl2Log("receive queue full, oldest packet dropped (%u so far)", dropped);
    }

    Mpl2DeliverPending(dev);

    if (stopping) {
        Mpl2TeardownStep(dev);
    } else {
        // Resubmit from a DPC, not from inside this completion routine.
        (void)KeInsertQueueDpc(&dev->ResubmitDpc, NULL, NULL);
    }
}

_Use_decl_annotations_
VOID
Mpl2Read(PMPL2_DEVICE Dev, WDFREQUEST Request, WDFFILEOBJECT File)
{
    NTSTATUS status;
    ULONG_PTR information = 0;
    ULONG length = 0;
    ULONG oversize = 0;
    BOOLEAN complete = TRUE;

    WdfSpinLockAcquire(Dev->Lock);
    if (Dev->State != Mpl2Open || Dev->Owner != File || Dev->Ring == NULL) {
        status = STATUS_DEVICE_NOT_CONNECTED;
    } else if (Dev->RingCount != 0) {
        status = Mpl2PopLocked(Dev, Request, &information, &length);
        oversize = Dev->Oversize;
    } else {
        status = WdfRequestForwardToIoQueue(Request, Dev->ReadQueue);
        complete = !NT_SUCCESS(status);
    }
    WdfSpinLockRelease(Dev->Lock);

    if (!complete) {
        return;
    }
    if (status == STATUS_BUFFER_TOO_SMALL) {
        Mpl2Log("packet of %u bytes bigger than the read buffer, dropped (%u so far)",
                length, oversize);
    } else if (!NT_SUCCESS(status)) {
        Mpl2Log("read failed 0x%08X", (ULONG)status);
    }
    WdfRequestCompleteWithInformation(Request, status, information);
}

// --------------------------------------------------------------------------
// Send

_Use_decl_annotations_
VOID
Mpl2Write(PMPL2_DEVICE Dev, WDFREQUEST Request, WDFFILEOBJECT File, size_t Length)
{
    NTSTATUS status;
    BOOLEAN open;
    L2CAP_CHANNEL_HANDLE channel;
    BTH_ADDR remote;
    USHORT outMtu;
    PVOID buffer = NULL;
    WDF_OBJECT_ATTRIBUTES attributes;
    WDFREQUEST tx = NULL;
    WDFMEMORY brbMemory = NULL;
    PMPL2_TX ctx;

    WdfSpinLockAcquire(Dev->Lock);
    open = Dev->State == Mpl2Open && Dev->Owner == File;
    channel = Dev->Channel;
    remote = Dev->Remote;
    outMtu = Dev->OutMtu;
    WdfSpinLockRelease(Dev->Lock);

    if (!open) {
        Mpl2Log("write failed: not connected");
        WdfRequestComplete(Request, STATUS_DEVICE_NOT_CONNECTED);
        return;
    }
    if (Length == 0 || Length > outMtu) {
        Mpl2Log("write of %Iu bytes refused, out mtu %u", Length, (ULONG)outMtu);
        WdfRequestComplete(Request, STATUS_INVALID_BUFFER_SIZE);
        return;
    }
    status = WdfRequestRetrieveInputBuffer(Request, Length, &buffer, NULL);
    if (!NT_SUCCESS(status) || buffer == NULL) {
        Mpl2Log("write: no input buffer 0x%08X", (ULONG)status);
        WdfRequestComplete(Request, NT_SUCCESS(status) ? STATUS_INVALID_PARAMETER : status);
        return;
    }

    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&attributes, MPL2_TX);
    attributes.ParentObject = Dev->Device;
    status = WdfRequestCreate(&attributes, Dev->Target, &tx);
    if (!NT_SUCCESS(status)) {
        Mpl2Log("write: WdfRequestCreate failed 0x%08X", (ULONG)status);
        WdfRequestComplete(Request, status);
        return;
    }
    ctx = Mpl2GetTx(tx);
    ctx->Dev = Dev;
    ctx->UserRequest = Request;
    Dev->Bth.BthInitializeBrb((PBRB)&ctx->Brb, BRB_L2CA_ACL_TRANSFER);
    ctx->Brb.BtAddress = remote;
    ctx->Brb.ChannelHandle = channel;
    ctx->Brb.TransferFlags = ACL_TRANSFER_DIRECTION_OUT;
    ctx->Brb.Buffer = buffer;
    ctx->Brb.BufferMDL = NULL;
    ctx->Brb.BufferSize = (ULONG)Length;

    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ParentObject = tx;
    status = WdfMemoryCreatePreallocated(&attributes, &ctx->Brb, sizeof(ctx->Brb), &brbMemory);
    if (NT_SUCCESS(status)) {
        status = Mpl2FormatBrb(Dev, tx, brbMemory);
    }
    if (NT_SUCCESS(status)) {
        WdfRequestSetCompletionRoutine(tx, Mpl2WriteComplete, ctx);
        if (WdfRequestSend(tx, Dev->Target, WDF_NO_SEND_OPTIONS)) {
            return;
        }
        status = WdfRequestGetStatus(tx);
    }
    Mpl2Log("write: sending failed 0x%08X", (ULONG)status);
    WdfObjectDelete(tx);
    WdfRequestComplete(Request, status);
}

_Use_decl_annotations_
static VOID
Mpl2WriteComplete(WDFREQUEST Request, WDFIOTARGET Target, PWDF_REQUEST_COMPLETION_PARAMS Params,
                  WDFCONTEXT Context)
{
    PMPL2_TX ctx = (PMPL2_TX)Context;
    PMPL2_DEVICE dev = ctx->Dev;
    WDFREQUEST user = ctx->UserRequest;
    NTSTATUS status = Params->IoStatus.Status;
    ULONG length = ctx->Brb.BufferSize;

    UNREFERENCED_PARAMETER(Target);

    if (NT_SUCCESS(status)) {
        WdfSpinLockAcquire(dev->Lock);
        dev->TxPackets++;
        WdfSpinLockRelease(dev->Lock);
    } else {
        Mpl2Log("write completed 0x%08X bt 0x%02X", (ULONG)status, (ULONG)ctx->Brb.Hdr.BtStatus);
        length = 0;
    }
    WdfObjectDelete(Request);
    WdfRequestCompleteWithInformation(user, status, length);
}
