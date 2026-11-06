# Captures

Real AirPods 3 recordings, anonymized (no Bluetooth address, no device name). Licence: CC BY 4.0
(see [`docs/LICENSE-CC-BY-4.0.txt`](../docs/LICENSE-CC-BY-4.0.txt)). Raw originals stay in
`captures/private/`, which is never committed.

Labelled recordings use `[[arrival_time_s, phase_label, remotehid_frame_hex], ...]` and can be
replayed with `gestures.load_recording()`.

| File | Content |
|---|---|
| `airpods3_motion_capture.json` | devmotion6 samples (head left/right, up/down, tilt) |
| `airpods3_glide_capture.json`, `airpods3_glide_raw*.json` | raw RemoteHID frames used for the glide experiments |
| `airpods3_announces_mtu*.hex` | RemoteHID device announcements at two L2CAP MTUs |
| `gestures_labeled.json` | labelled gestures at ~201 Hz raw IMU: silence, teeth clicks (single/double), chewing, talking, miming, nod, shake, tilts, look up/down |
| `cma_walk.json` | sitting / walking / standing / jogging in place / sitting, devmotion6 ~50 Hz (false-positive check) |
| `inventory_micro.json` | microphone-stream inventory (15 frames, labels silence / parle / fredonne / dents / mache) |
| `inventory_musique.json` | **labels are INVALID**: the prompts (silence, parle, fredonne, dents, mache) were shown but not performed while music was playing. Use it only as "no voluntary gesture" data, as `tests/test_gestures_real.py` does (false-positive check). |
