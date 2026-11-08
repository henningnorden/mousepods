# Head-tracking DLLs for Proton/Wine games

`NPClient(64).dll` (TrackIR protocol) and `freetrackclient(64).dll` (FreeTrack protocol). MousePods
places them in a game's Proton prefix (`drive_c/mousepods/`) and points the NaturalPoint and
FreeTrack registry keys at them. The game loads them itself; they read the head pose from the file
named in `HKCU\Software\MousePods\PoseFile` (default `Z:\dev\shm\mousepods-headpose`).

Pose file: 64 bytes, little-endian — `u32 seq`, `u32 version (1)`, `f64 x, y, z` (cm),
`f64 yaw, pitch, roll` (degrees, opentrack convention), `f64 t` (monotonic seconds).

Build: `native/build.sh` (downloads zig to `~/.cache/mousepods-build`, sha256-checked; output in `native/out/`). Debug log: set
`HKCU\Software\MousePods\Debug` (DWORD) to 1 → `C:\mousepods\npclient.log`.

Licences: see `NOTICE` (opentrack ISC, linux-track MIT, MousePods Apache-2.0).

Freshness: `seq` changes with every write (~120/s). If it stops changing for 1.5 s
(`native/pose_stale.h`), the DLLs report a centred pose, so the camera does not freeze at the last
head angle when MousePods quits.

The pose files (`/dev/shm/mousepods-<uid>/pose-<appid>`) are deliberately left in place when
MousePods exits: a running game keeps its file mapped, and a restarted MousePods writes into the
same file again. They disappear at reboot or when the game is unticked.
