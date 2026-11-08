/* MousePods FreeTrackClient for Proton/Wine games: reads the head pose MousePods writes to a
 * Linux file (path from HKCU\Software\MousePods\PoseFile, via Wine's Z: drive).
 * Exports and types follow opentrack's freetrackclient/freetrackclient.c
 * (opentrack, Copyright (c) Stanislaw Halik and contributors, ISC licence; FreeTrack protocol by
 * the FreeTrack team). Copyright 2026 Henning Norden, Apache-2.0. See NOTICE.
 */
#define _USE_MATH_DEFINES
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <windows.h>
#include "fttypes.h"

#define FT_EXPORT(t) __declspec(dllexport) t
#define POSE_PATH_DEFAULT "Z:\\dev\\shm\\mousepods-headpose"

static void pose_path(char *out, DWORD size)
{
    HKEY k; DWORD type = 0, sz = size;
    strcpy(out, POSE_PATH_DEFAULT);
    if (RegOpenKeyExA(HKEY_CURRENT_USER, "Software\\MousePods", 0, KEY_READ, &k) == ERROR_SUCCESS) {
        if (RegQueryValueExA(k, "PoseFile", NULL, &type, (LPBYTE) out, &sz) != ERROR_SUCCESS || type != REG_SZ || !out[0])
            strcpy(out, POSE_PATH_DEFAULT);
        RegCloseKey(k);
    }
}

static const volatile unsigned char *pPose = NULL;
static unsigned int dataId = 0;
static FILE *g_log = NULL;

static int debug_on = -1;

static void lg(const char *msg, unsigned long v)
{
    if (debug_on < 0) {
        HKEY k; DWORD d = 0, sz = sizeof d, type = 0;
        debug_on = 0;
        if (RegOpenKeyExA(HKEY_CURRENT_USER, "Software\\MousePods", 0, KEY_READ, &k) == ERROR_SUCCESS) {
            if (RegQueryValueExA(k, "Debug", NULL, &type, (LPBYTE) &d, &sz) == ERROR_SUCCESS && type == REG_DWORD)
                debug_on = d == 1;
            RegCloseKey(k);
        }
    }
    if (!debug_on) return;
    if (!g_log) { CreateDirectoryA("C:\\mousepods", NULL); g_log = fopen("C:\\mousepods\\freetrackclient.log", "a"); }
    if (g_log) { fprintf(g_log, "%s %lu\n", msg, v); fflush(g_log); }
}

static BOOL pose_map(void)
{
    if (pPose) return TRUE;
    char path[MAX_PATH];
    pose_path(path, sizeof path);
    HANDLE f = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) { lg("open failed, err", GetLastError()); return FALSE; }
    HANDLE m = CreateFileMappingA(f, NULL, PAGE_READONLY, 0, 64, NULL);
    pPose = m ? (const volatile unsigned char *) MapViewOfFile(m, FILE_MAP_READ, 0, 0, 64) : NULL;
    lg(pPose ? "mapped pose file" : "map failed, err", pPose ? 0 : GetLastError());
    return pPose != NULL;
}

FT_EXPORT(BOOL) FTGetData(FTData *data)
{
    double v[6];
    if (!pose_map()) return FALSE;
    for (int i = 0; i < 6; i++) memcpy(&v[i], (const void *)(pPose + 8 + 8 * i), 8);
    memset((void *) data, 0, sizeof(FTData));
    data->DataID = ++dataId;
    data->CamWidth = 250; data->CamHeight = 100;
    data->Yaw = (float)(-v[3] * M_PI / 180.); data->Pitch = (float)(-v[4] * M_PI / 180.); data->Roll = (float)(v[5] * M_PI / 180.);
    data->X = (float)(v[0] * 10.); data->Y = (float)(v[1] * 10.); data->Z = (float)(v[2] * 10.);
    data->RawYaw = data->Yaw; data->RawPitch = data->Pitch; data->RawRoll = data->Roll;
    if (dataId % 1000 == 1) lg("FTGetData frame", dataId);
    return TRUE;
}

FT_EXPORT(void) FTReportName(int name) { lg("FTReportName", (unsigned long) name); }
FT_EXPORT(void) FTReportID(int name) { lg("FTReportID", (unsigned long) name); }
FT_EXPORT(const char *) FTGetDllVersion(void) { return "1.0.0.0"; }
FT_EXPORT(const char *) FTProvider(void) { return "FreeTrack"; }
