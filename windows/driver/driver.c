// SPDX-License-Identifier: MS-PL
// Copyright (c) Microsoft Corporation (Windows-driver-samples bthecho), modified for
// MousePods. Licensed under the Microsoft Public License, see LICENSE-MS-PL.txt.
// MousePods L2CAP driver: entry point.
//
// Structure follows Microsoft's Windows-driver-samples bluetooth/bthecho
// (MS-PL).

#include <ntddk.h>
#include <wdf.h>
// GUIDs from public.h and the Bluetooth headers are instantiated here only.
#include <initguid.h>

#include "driver.h"

_Use_decl_annotations_
NTSTATUS
DriverEntry(PDRIVER_OBJECT DriverObject, PUNICODE_STRING RegistryPath)
{
    WDF_DRIVER_CONFIG config;
    NTSTATUS status;

    Mpl2LogInit();
    Mpl2Log("driver loaded, interface version %u", (ULONG)MPL2_VERSION);

    WDF_DRIVER_CONFIG_INIT(&config, Mpl2EvtDeviceAdd);
    config.DriverPoolTag = MPL2_POOLTAG;

    status = WdfDriverCreate(DriverObject, RegistryPath, WDF_NO_OBJECT_ATTRIBUTES,
                             &config, WDF_NO_HANDLE);
    if (!NT_SUCCESS(status)) {
        Mpl2Log("WdfDriverCreate failed 0x%08X", (ULONG)status);
    }
    return status;
}

_Use_decl_annotations_
NTSTATUS
Mpl2EvtDeviceAdd(WDFDRIVER Driver, PWDFDEVICE_INIT DeviceInit)
{
    UNREFERENCED_PARAMETER(Driver);
    return Mpl2DeviceCreate(DeviceInit);
}
