// SPDX-License-Identifier: MS-PL
// Copyright (c) Microsoft Corporation (Windows-driver-samples bthecho), modified for
// MousePods. Licensed under the Microsoft Public License, see LICENSE-MS-PL.txt.
// MousePods L2CAP driver: device creation and the route to the Bluetooth stack.
//
// Route 2 (bthenum): the driver is loaded by BTHENUM for a service GUID and
// gets the profile driver interface from its own stack, as in Microsoft's
// Windows-driver-samples bluetooth/bthecho (MS-PL). Route 1 (root): a root-enumerated device opens the
// local radio (GUID_BTHPORT_DEVICE_INTERFACE) as a remote I/O target and
// queries the same interface from it with IRP_MN_QUERY_INTERFACE.

#include "driver.h"

_IRQL_requires_max_(PASSIVE_LEVEL)
static NTSTATUS
Mpl2QueryRadioInterface(_In_ PMPL2_DEVICE Dev)
{
    NTSTATUS status;

    RtlZeroMemory(&Dev->Bth, sizeof(Dev->Bth));
    status = WdfIoTargetQueryForInterface(Dev->Target,
                                          &GUID_BTHDDI_PROFILE_DRIVER_INTERFACE,
                                          (PINTERFACE)&Dev->Bth,
                                          (USHORT)sizeof(Dev->Bth),
                                          BTHDDI_PROFILE_DRIVER_INTERFACE_VERSION_FOR_QI,
                                          NULL);
    if (!NT_SUCCESS(status)) {
        Mpl2Log("route root: query interface on the radio failed 0x%08X", (ULONG)status);
    }
    return status;
}

_IRQL_requires_max_(PASSIVE_LEVEL)
static NTSTATUS
Mpl2OpenRadioTarget(_In_ PMPL2_DEVICE Dev)
{
    NTSTATUS status;
    PZZWSTR list = NULL;
    UNICODE_STRING name;
    WDF_OBJECT_ATTRIBUTES attributes;
    WDF_IO_TARGET_OPEN_PARAMS openParams;
    WDFIOTARGET target = NULL;

    status = IoGetDeviceInterfaces(&GUID_BTHPORT_DEVICE_INTERFACE, NULL, 0, &list);
    if (!NT_SUCCESS(status)) {
        Mpl2Log("route root: no radio interface list 0x%08X", (ULONG)status);
        return status;
    }
    if (list == NULL || list[0] == L'\0') {
        Mpl2Log("route root: no Bluetooth radio present");
        if (list != NULL) {
            ExFreePool(list);
        }
        return STATUS_NOT_FOUND;
    }

    // The first radio in the list.
    RtlInitUnicodeString(&name, list);

    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ParentObject = Dev->Device;
    status = WdfIoTargetCreate(Dev->Device, &attributes, &target);
    if (!NT_SUCCESS(status)) {
        Mpl2Log("route root: WdfIoTargetCreate failed 0x%08X", (ULONG)status);
        ExFreePool(list);
        return status;
    }

    WDF_IO_TARGET_OPEN_PARAMS_INIT_OPEN_BY_NAME(&openParams, &name,
                                                GENERIC_READ | GENERIC_WRITE);
    openParams.EvtIoTargetQueryRemove = Mpl2EvtTargetQueryRemove;
    openParams.EvtIoTargetRemoveCanceled = Mpl2EvtTargetRemoveCanceled;
    openParams.EvtIoTargetRemoveComplete = Mpl2EvtTargetRemoveComplete;

    status = WdfIoTargetOpen(target, &openParams);
    ExFreePool(list);
    if (!NT_SUCCESS(status)) {
        Mpl2Log("route root: opening the radio failed 0x%08X", (ULONG)status);
        WdfObjectDelete(target);
        return status;
    }

    Dev->Target = target;
    Dev->RemoteTarget = TRUE;
    return STATUS_SUCCESS;
}

// Route detection, called from EvtDeviceAdd. The device starts even if both
// routes fail; CONNECT then reports stage 1 with the lookup status.
_IRQL_requires_max_(PASSIVE_LEVEL)
static VOID
Mpl2DetectRoute(_In_ PMPL2_DEVICE Dev)
{
    NTSTATUS status;

    RtlZeroMemory(&Dev->Bth, sizeof(Dev->Bth));
    status = WdfFdoQueryForInterface(Dev->Device,
                                     &GUID_BTHDDI_PROFILE_DRIVER_INTERFACE,
                                     (PINTERFACE)&Dev->Bth,
                                     (USHORT)sizeof(Dev->Bth),
                                     BTHDDI_PROFILE_DRIVER_INTERFACE_VERSION_FOR_QI,
                                     NULL);
    if (NT_SUCCESS(status)) {
        Dev->Route = MPL2_ROUTE_BTHENUM;
        Dev->Target = WdfDeviceGetIoTarget(Dev->Device);
        Dev->HaveInterface = TRUE;
        Mpl2Log("route bthenum: profile driver interface from own stack");
    } else {
        Mpl2Log("route bthenum not available 0x%08X, trying the radio", (ULONG)status);
        status = Mpl2OpenRadioTarget(Dev);
        if (NT_SUCCESS(status)) {
            status = Mpl2QueryRadioInterface(Dev);
            if (NT_SUCCESS(status)) {
                Dev->Route = MPL2_ROUTE_ROOT;
                Dev->HaveInterface = TRUE;
                Mpl2Log("route root: profile driver interface from the radio");
            } else {
                WdfIoTargetClose(Dev->Target);
            }
        }
    }

    Dev->LookupStatus = status;
    if (!NT_SUCCESS(status)) {
        Dev->Route = MPL2_ROUTE_NONE;
        Mpl2Log("no route to the Bluetooth stack 0x%08X", (ULONG)status);
        return;
    }

    status = Mpl2BrbRequestsCreate(Dev);
    if (!NT_SUCCESS(status)) {
        Dev->LookupStatus = status;
        Mpl2Log("creating BRB requests failed 0x%08X", (ULONG)status);
    }
}

_IRQL_requires_max_(PASSIVE_LEVEL)
static VOID
Mpl2ReleaseInterface(_In_ PMPL2_DEVICE Dev)
{
    BOOLEAN had;

    WdfSpinLockAcquire(Dev->Lock);
    had = Dev->HaveInterface;
    Dev->HaveInterface = FALSE;
    WdfSpinLockRelease(Dev->Lock);

    if (had && Dev->Bth.Interface.InterfaceDereference != NULL) {
        Dev->Bth.Interface.InterfaceDereference(Dev->Bth.Interface.Context);
    }
}

_Use_decl_annotations_
NTSTATUS
Mpl2DeviceCreate(PWDFDEVICE_INIT DeviceInit)
{
    NTSTATUS status;
    WDFDEVICE device;
    PMPL2_DEVICE dev;
    WDF_OBJECT_ATTRIBUTES attributes;
    WDF_PNPPOWER_EVENT_CALLBACKS pnpCallbacks;
    WDF_FILEOBJECT_CONFIG fileConfig;
    DECLARE_CONST_UNICODE_STRING(sddl, MPL2_SDDL);

    WdfDeviceInitSetIoType(DeviceInit, WdfDeviceIoBuffered);
    WdfDeviceInitSetCharacteristics(DeviceInit, FILE_DEVICE_SECURE_OPEN, TRUE);

    // Same ACL as the INF: system, administrators, interactive user.
    status = WdfDeviceInitAssignSDDLString(DeviceInit, &sddl);
    if (!NT_SUCCESS(status)) {
        Mpl2Log("assigning the device ACL failed 0x%08X", (ULONG)status);
        return status;
    }

    WDF_PNPPOWER_EVENT_CALLBACKS_INIT(&pnpCallbacks);
    pnpCallbacks.EvtDeviceSelfManagedIoInit = Mpl2EvtSelfManagedIoInit;
    pnpCallbacks.EvtDeviceSelfManagedIoSuspend = Mpl2EvtSelfManagedIoSuspend;
    pnpCallbacks.EvtDeviceSelfManagedIoRestart = Mpl2EvtSelfManagedIoRestart;
    pnpCallbacks.EvtDeviceSelfManagedIoCleanup = Mpl2EvtSelfManagedIoCleanup;
    WdfDeviceInitSetPnpPowerEventCallbacks(DeviceInit, &pnpCallbacks);

    WDF_FILEOBJECT_CONFIG_INIT(&fileConfig, WDF_NO_EVENT_CALLBACK, WDF_NO_EVENT_CALLBACK,
                               Mpl2EvtFileCleanup);
    WdfDeviceInitSetFileObjectConfig(DeviceInit, &fileConfig, WDF_NO_OBJECT_ATTRIBUTES);

    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&attributes, MPL2_DEVICE);
    status = WdfDeviceCreate(&DeviceInit, &attributes, &device);
    if (!NT_SUCCESS(status)) {
        Mpl2Log("WdfDeviceCreate failed 0x%08X", (ULONG)status);
        return status;
    }

    dev = Mpl2GetDevice(device);
    dev->Device = device;
    dev->State = Mpl2Idle;
    KeInitializeEvent(&dev->IdleEvent, NotificationEvent, TRUE);
    KeInitializeDpc(&dev->ResubmitDpc, Mpl2ResubmitDpc, dev);

    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ParentObject = device;
    status = WdfSpinLockCreate(&attributes, &dev->Lock);
    if (!NT_SUCCESS(status)) {
        Mpl2Log("WdfSpinLockCreate failed 0x%08X", (ULONG)status);
        return status;
    }

    status = Mpl2QueuesCreate(dev);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    status = WdfDeviceCreateDeviceInterface(device, &GUID_DEVINTERFACE_MOUSEPODS_L2CAP, NULL);
    if (!NT_SUCCESS(status)) {
        Mpl2Log("WdfDeviceCreateDeviceInterface failed 0x%08X", (ULONG)status);
        return status;
    }

    Mpl2DetectRoute(dev);
    Mpl2Log("device added, route %u", dev->Route);
    return STATUS_SUCCESS;
}

_Use_decl_annotations_
NTSTATUS
Mpl2EvtSelfManagedIoInit(WDFDEVICE Device)
{
    PMPL2_DEVICE dev = Mpl2GetDevice(Device);

    if (dev->HaveInterface) {
        (void)Mpl2ReadRadioAddress(dev);
    }
    Mpl2Log("device started");
    return STATUS_SUCCESS;
}

_Use_decl_annotations_
NTSTATUS
Mpl2EvtSelfManagedIoSuspend(WDFDEVICE Device)
{
    PMPL2_DEVICE dev = Mpl2GetDevice(Device);

    Mpl2Log("device suspend (power down or removal)");
    Mpl2CloseAndWait(dev, NULL, "device power down or removal");
    return STATUS_SUCCESS;
}

_Use_decl_annotations_
NTSTATUS
Mpl2EvtSelfManagedIoRestart(WDFDEVICE Device)
{
    UNREFERENCED_PARAMETER(Device);
    Mpl2Log("device restart");
    return STATUS_SUCCESS;
}

_Use_decl_annotations_
VOID
Mpl2EvtSelfManagedIoCleanup(WDFDEVICE Device)
{
    PMPL2_DEVICE dev = Mpl2GetDevice(Device);

    Mpl2CloseAndWait(dev, NULL, "device removal");
    KeFlushQueuedDpcs();
    Mpl2ReleaseInterface(dev);
    if (dev->RemoteTarget) {
        WdfIoTargetClose(dev->Target);
    }
    Mpl2Log("device removed");
}

// Route 1: the radio is going away (or asked whether it may).
_Use_decl_annotations_
NTSTATUS
Mpl2EvtTargetQueryRemove(WDFIOTARGET IoTarget)
{
    PMPL2_DEVICE dev = Mpl2GetDevice(WdfIoTargetGetDevice(IoTarget));

    Mpl2Log("radio query remove");
    Mpl2CloseAndWait(dev, NULL, "radio removal");
    Mpl2ReleaseInterface(dev);
    WdfIoTargetCloseForQueryRemove(IoTarget);
    return STATUS_SUCCESS;
}

_Use_decl_annotations_
VOID
Mpl2EvtTargetRemoveCanceled(WDFIOTARGET IoTarget)
{
    PMPL2_DEVICE dev = Mpl2GetDevice(WdfIoTargetGetDevice(IoTarget));
    WDF_IO_TARGET_OPEN_PARAMS openParams;
    NTSTATUS status;

    WDF_IO_TARGET_OPEN_PARAMS_INIT_REOPEN(&openParams);
    status = WdfIoTargetOpen(IoTarget, &openParams);
    if (NT_SUCCESS(status)) {
        status = Mpl2QueryRadioInterface(dev);
    }
    if (NT_SUCCESS(status)) {
        WdfSpinLockAcquire(dev->Lock);
        dev->HaveInterface = TRUE;
        WdfSpinLockRelease(dev->Lock);
        Mpl2Log("radio removal cancelled, reopened");
    } else {
        dev->LookupStatus = status;
        Mpl2Log("radio removal cancelled, reopening failed 0x%08X", (ULONG)status);
    }
}

_Use_decl_annotations_
VOID
Mpl2EvtTargetRemoveComplete(WDFIOTARGET IoTarget)
{
    PMPL2_DEVICE dev = Mpl2GetDevice(WdfIoTargetGetDevice(IoTarget));

    Mpl2Log("radio removed");
    Mpl2CloseAndWait(dev, NULL, "radio removed");
    Mpl2ReleaseInterface(dev);
    dev->LookupStatus = STATUS_DEVICE_REMOVED;
    WdfIoTargetClose(IoTarget);
}
