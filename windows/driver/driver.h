// SPDX-License-Identifier: Apache-2.0
// MousePods L2CAP driver: shared declarations.
//
// A thin pipe: it opens one L2CAP channel on PSM 0x1001 (AAP) to a paired
// device and moves whole packets between the channel and ReadFile/WriteFile.
// All protocol logic stays in Python.
//
// BRB handling follows Microsoft's Windows-driver-samples bluetooth/bthecho
// (MIT licence, Copyright (c) Microsoft Corporation), see windows/README.md.

#pragma once

#include <ntddk.h>
#include <wdf.h>
#include <ntstrsafe.h>
#include <bthdef.h>
#include <bthguid.h>
#include <bthioctl.h>
#include <bthddi.h>

#include "public.h"
#include "log.h"

#define MPL2_POOLTAG 'lPpM'

// Receive queue: up to this many packets wait for ReadFile; the oldest is
// dropped when it is full.
#define MPL2_RX_SLOTS 64

// CONNECT without a timeout waits this long.
#define MPL2_DEFAULT_CONNECT_TIMEOUT_MS 15000

// How long cleanup and power-down wait for the channel to close.
#define MPL2_TEARDOWN_WAIT_MS 5000

// MPL2_INFO.route
#define MPL2_ROUTE_NONE 0
#define MPL2_ROUTE_ROOT 1
#define MPL2_ROUTE_BTHENUM 2

// Device ACL: system, administrators, the interactive user (read/write).
#define MPL2_SDDL L"D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)"

// CONNECT completes in one of two ways:
//  - refused before anything is sent (wrong PSM: STATUS_ACCESS_DENIED, a
//    channel already open: STATUS_DEVICE_BUSY, bad arguments:
//    STATUS_INVALID_PARAMETER / STATUS_BUFFER_TOO_SMALL): the IOCTL itself
//    fails with that status (DeviceIoControl returns FALSE); this is stage 0.
//  - an attempt was made: the IOCTL succeeds and MPL2_CONNECT_OUT carries the
//    result; ntstatus == 0 means the channel is open, otherwise stage says
//    which step failed. bt_status is Hdr.BtStatus in the low byte; when the
//    remote refused (STATUS_REQUEST_NOT_ACCEPTED) its L2CAP connect result
//    is in the upper 16 bits.

typedef enum _MPL2_CHANNEL_STATE {
    Mpl2Idle = 0,
    Mpl2Connecting,
    Mpl2Open,
    Mpl2Closing,
} MPL2_CHANNEL_STATE;

typedef struct _MPL2_DEVICE {
    WDFDEVICE Device;

    // Route and the interface used to send BRBs.
    ULONG Route;
    NTSTATUS LookupStatus;
    WDFIOTARGET Target;             // default target (route 2) or remote (route 1)
    BOOLEAN RemoteTarget;           // Target was created by us (route 1)
    BOOLEAN HaveInterface;          // Bth below is valid and referenced
    BOOLEAN RequestsReady;          // the BRB requests below exist
    BTH_PROFILE_DRIVER_INTERFACE Bth;
    BTH_ADDR RadioAddr;

    // Everything below is protected by Lock.
    WDFSPINLOCK Lock;
    MPL2_CHANNEL_STATE State;
    WDFFILEOBJECT Owner;            // handle that opened the channel
    BOOLEAN AbortConnect;           // close as soon as the pending open completes
    BTH_ADDR Remote;
    L2CAP_CHANNEL_HANDLE Channel;
    USHORT OutMtu;
    USHORT InMtu;
    USHORT BufMtu;                  // size of one receive slot

    KEVENT IdleEvent;               // signalled while State == Mpl2Idle

    // Connect.
    WDFREQUEST ConnectReq;
    WDFMEMORY ConnectBrbMem;
    struct _BRB_L2CA_OPEN_CHANNEL OpenBrb;
    WDFREQUEST UserConnect;         // the pending IOCTL_MPL2_CONNECT

    // Close.
    WDFREQUEST CloseReq;
    WDFMEMORY CloseBrbMem;
    struct _BRB_L2CA_CLOSE_CHANNEL CloseBrb;
    WDFREQUEST UserDisconnect;      // the pending IOCTL_MPL2_DISCONNECT
    LONG TeardownPending;           // close BRB + reader still to finish

    // Continuous reader.
    WDFREQUEST ReadReq;
    WDFMEMORY ReadBrbMem;
    struct _BRB_L2CA_ACL_TRANSFER ReadBrb;
    BOOLEAN ReaderRunning;
    BOOLEAN ReaderStop;
    KDPC ResubmitDpc;

    // Receive buffer for the reader and the packet queue.
    WDFMEMORY RxMem;
    PUCHAR RxData;
    WDFMEMORY RingMem;
    PUCHAR Ring;
    USHORT RingLen[MPL2_RX_SLOTS];
    ULONG RingHead;
    ULONG RingCount;

    // Counters for the current channel.
    ULONG RxPackets;
    ULONG TxPackets;
    ULONG Dropped;                  // queue full, oldest dropped
    ULONG Oversize;                 // packet bigger than the read buffer

    // Reads wait here while the queue is empty.
    WDFQUEUE ReadQueue;
} MPL2_DEVICE, *PMPL2_DEVICE;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(MPL2_DEVICE, Mpl2GetDevice)

// One WriteFile in flight.
typedef struct _MPL2_TX {
    PMPL2_DEVICE Dev;
    WDFREQUEST UserRequest;
    struct _BRB_L2CA_ACL_TRANSFER Brb;
} MPL2_TX, *PMPL2_TX;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(MPL2_TX, Mpl2GetTx)

// driver.c
DRIVER_INITIALIZE DriverEntry;
EVT_WDF_DRIVER_DEVICE_ADD Mpl2EvtDeviceAdd;

// device.c
_IRQL_requires_max_(PASSIVE_LEVEL)
NTSTATUS
Mpl2DeviceCreate(_Inout_ PWDFDEVICE_INIT DeviceInit);

EVT_WDF_DEVICE_SELF_MANAGED_IO_INIT Mpl2EvtSelfManagedIoInit;
EVT_WDF_DEVICE_SELF_MANAGED_IO_SUSPEND Mpl2EvtSelfManagedIoSuspend;
EVT_WDF_DEVICE_SELF_MANAGED_IO_RESTART Mpl2EvtSelfManagedIoRestart;
EVT_WDF_DEVICE_SELF_MANAGED_IO_CLEANUP Mpl2EvtSelfManagedIoCleanup;
EVT_WDF_IO_TARGET_QUERY_REMOVE Mpl2EvtTargetQueryRemove;
EVT_WDF_IO_TARGET_REMOVE_CANCELED Mpl2EvtTargetRemoveCanceled;
EVT_WDF_IO_TARGET_REMOVE_COMPLETE Mpl2EvtTargetRemoveComplete;

// queue.c
_IRQL_requires_max_(PASSIVE_LEVEL)
NTSTATUS
Mpl2QueuesCreate(_In_ PMPL2_DEVICE Dev);

EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL Mpl2EvtIoDeviceControl;
EVT_WDF_IO_QUEUE_IO_READ Mpl2EvtIoRead;
EVT_WDF_IO_QUEUE_IO_WRITE Mpl2EvtIoWrite;
EVT_WDF_FILE_CLEANUP Mpl2EvtFileCleanup;

// l2cap.c
_IRQL_requires_max_(PASSIVE_LEVEL)
NTSTATUS
Mpl2BrbRequestsCreate(_In_ PMPL2_DEVICE Dev);

_IRQL_requires_max_(PASSIVE_LEVEL)
NTSTATUS
Mpl2ReadRadioAddress(_In_ PMPL2_DEVICE Dev);

_IRQL_requires_max_(DISPATCH_LEVEL)
VOID
Mpl2Connect(_In_ PMPL2_DEVICE Dev, _In_ WDFREQUEST Request, _In_ WDFFILEOBJECT File);

_IRQL_requires_max_(DISPATCH_LEVEL)
VOID
Mpl2Disconnect(_In_ PMPL2_DEVICE Dev, _In_ WDFREQUEST Request, _In_ WDFFILEOBJECT File);

_IRQL_requires_max_(DISPATCH_LEVEL)
BOOLEAN
Mpl2BeginTeardown(_In_ PMPL2_DEVICE Dev, _In_z_ PCSTR Reason);

_IRQL_requires_max_(PASSIVE_LEVEL)
VOID
Mpl2CloseAndWait(_In_ PMPL2_DEVICE Dev, _In_opt_ WDFFILEOBJECT OnlyOwner, _In_z_ PCSTR Reason);

_IRQL_requires_max_(DISPATCH_LEVEL)
VOID
Mpl2Read(_In_ PMPL2_DEVICE Dev, _In_ WDFREQUEST Request, _In_ WDFFILEOBJECT File);

_IRQL_requires_max_(DISPATCH_LEVEL)
VOID
Mpl2Write(_In_ PMPL2_DEVICE Dev, _In_ WDFREQUEST Request, _In_ WDFFILEOBJECT File, _In_ size_t Length);

KDEFERRED_ROUTINE Mpl2ResubmitDpc;
