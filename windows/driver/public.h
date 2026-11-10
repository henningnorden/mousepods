// SPDX-License-Identifier: Apache-2.0
// Interface between the MousePods L2CAP driver and user mode.
// Shared by the driver and docs; l2cap_win.py mirrors it (tests keep them in sync).
// All structs are packed to 8 bytes, little endian.

#pragma once

#ifndef CTL_CODE
#include <winioctl.h>
#endif

// Device interface class: {6d0e3c52-4f1b-4c9a-9a51-2f0b8e7d1a01}
DEFINE_GUID(GUID_DEVINTERFACE_MOUSEPODS_L2CAP,
    0x6d0e3c52, 0x4f1b, 0x4c9a, 0x9a, 0x51, 0x2f, 0x0b, 0x8e, 0x7d, 0x1a, 0x01);

#define MPL2_PSM_AAP 0x1001
#define MPL2_VERSION 1

// Info: version, route (1 root, 2 bthenum), local radio address.
#define IOCTL_MPL2_INFO \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x800, METHOD_BUFFERED, FILE_READ_ACCESS)
// Connect: in MPL2_CONNECT_IN, out MPL2_CONNECT_OUT.
#define IOCTL_MPL2_CONNECT \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x801, METHOD_BUFFERED, FILE_READ_ACCESS | FILE_WRITE_ACCESS)
// Disconnect: no buffers.
#define IOCTL_MPL2_DISCONNECT \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x802, METHOD_BUFFERED, FILE_ANY_ACCESS)
// Log: out is UTF-8 text of the driver's ring log (16 KiB ring, oldest dropped).
#define IOCTL_MPL2_LOG \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x803, METHOD_BUFFERED, FILE_READ_ACCESS)

// MPL2_CONNECT_OUT.stage: where a connect attempt stopped.
#define MPL2_STAGE_LOOKUP   1  // interface/radio lookup
#define MPL2_STAGE_BRB      2  // BRB submit
#define MPL2_STAGE_REMOTE   3  // remote response
#define MPL2_STAGE_CONFIG   4  // configuration
#define MPL2_STAGE_OPEN     5  // open

// MPL2_CONNECT_IN.security
#define MPL2_SEC_NONE 0
#define MPL2_SEC_AUTH 1
#define MPL2_SEC_AUTH_ENCRYPT 2

// ReadFile returns one whole received packet; WriteFile sends one whole
// packet (at most out_mtu bytes).

#pragma pack(push, 8)

typedef struct _MPL2_INFO {
    UINT32 version;
    UINT32 route;       // 1 root, 2 bthenum
    UINT64 radio_addr;
} MPL2_INFO;

typedef struct _MPL2_CONNECT_IN {
    UINT64 bth_addr;
    UINT16 psm;
    UINT16 mtu;
    UINT32 security;    // 0 none, 1 auth, 2 auth+encrypt
    UINT32 timeout_ms;
} MPL2_CONNECT_IN;

typedef struct _MPL2_CONNECT_OUT {
    INT32  ntstatus;
    UINT32 bt_status;
    UINT32 stage;
    UINT16 out_mtu;
    UINT16 reserved;
} MPL2_CONNECT_OUT;

#pragma pack(pop)
