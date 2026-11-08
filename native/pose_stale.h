/* MousePods pose file freshness. Copyright 2026 Henning Norden, Apache-2.0.
 *
 * The pose file's seq counter changes with every write (~120/s). If it stops changing for
 * POSE_STALE_MS, MousePods is gone (quit, crashed, AirPods out of range for long): the DLLs then
 * report a centred pose instead of freezing the camera wherever the head last was.
 * A zero-initialised PoseStale means "nothing read yet".
 */
#pragma once
#include <stdint.h>

#define POSE_STALE_MS 1500

typedef struct PoseStale {
    int seen;
    uint32_t last_seq;
    uint64_t last_change_ms;
} PoseStale;

static inline int pose_fresh(PoseStale *st, uint32_t seq, uint64_t now_ms)
{
    if (!st->seen || seq != st->last_seq) {
        st->seen = 1;
        st->last_seq = seq;
        st->last_change_ms = now_ms;
        return 1;
    }
    return now_ms - st->last_change_ms <= POSE_STALE_MS;
}
