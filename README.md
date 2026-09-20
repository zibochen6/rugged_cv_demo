# Rugged J401 Three-Camera Visual Hub

**English** · [中文](README.zh.md)

A **three-camera visual hub plus a pure recording centre** for forklifts and in-plant vehicles: front-view
click-to-segment, rear-view monocular depth collision warning, and cabin fatigue/helmet monitoring. Each module
picks its own camera from the ones the hub can detect, every module starts on demand, and everything can be
released with a single command. The device also has a same-page desktop full-screen entry.

![Visual Hub console: three camera cards, the rear safety panel and the unified event stream](docs/images/dashboard.png)

*The three-camera console: front click-to-segment, rear depth warning with its live SAFE/WARNING/DANGER panel,
and cabin helmet/fatigue detection. Each card carries its own camera selector and is bound to one of the cameras
the hub detected — see [Choose a camera per module](#5-choose-a-camera-per-module) for how the roles share the
available hardware.*

| Item | Value |
| --- | --- |
| Device | reComputer Rugged J401 (Seeed) |
| Compute | NVIDIA Jetson Orin NX 16GB |
| OS | JetPack 5.1.3 / L4T R35.5.0 / Ubuntu 20.04 |
| Runtime | Python 3.8.10 (project-local `.venv`), CUDA 11.4, TensorRT 8.5 |
| PyTorch | `2.1.0a0+41361538.nv23.6` (NVIDIA jp5 redist wheel) |
| Cameras | 2× PoE RTSP + 1× USB 1080p by default; **any role can be re-bound to any detected camera from the UI** |

**Single user entry point:**

```text
http://<Jetson LAN IP>:8000/
```

> **Safety boundary — read this first.** This is a **driving/operation assistance prototype**, not a
> functional-safety system. It does not replace the operator's own observation, promises nothing about
> "collision avoidance", holds no safety certification, and must not be used for compliance decisions.
> While perception is unreliable it must display `SYSTEM ERROR` and **never silently default to SAFE**.
> See [Security](#security).

## Contents

[System composition](#system-composition) · [Features](#features) · [Repository layout](#repository-layout) ·
[Installation and deployment](#installation-and-deployment) · [Running and operations](#running-and-operations) ·
[Configuration](#configuration) · [HTTP API](#http-api) · [Logs and retention](#logs-and-retention) ·
[Tests](#tests) · [Troubleshooting](#troubleshooting) · [Security](#security) ·
[Backup and restore](#backup-and-restore) · [Documentation](#documentation)

## Quick start

```bash
cd /home/seeed/workspace/seg_demo

sudo ./deploy/install.sh          # one-time: systemd units / logrotate / passwordless sudo / desktop icon
sudo systemctl start visual-hub   # start the hub (already enabled at boot)
# open http://<Jetson IP>:8000/ in a browser and press "Start All"

./scripts/run_visual_hub.sh stop  # stop recording + all three modules, release cameras/GPU/ports
./scripts/run_visual_hub.sh status
```

## System composition

| Module | Camera | Process model | Port | Capability |
| --- | --- | --- | --- | --- |
| **Front segmentation** `front` | `FRONT_CAMERA_URL` (PoE RTSP) · re-bindable | in-process | public **8000** | EfficientTAM click-to-segment, memory tracking, verified re-lock when the target reappears |
| **Rear warning** `rear` | `REAR_CAMERA_URL` (PoE RTSP) · re-bindable | child `app/warn_app.py` | loopback **8080** | monocular metric depth, obstacle + person channels, `SAFE/WARNING/DANGER/SYSTEM_ERROR` |
| **Cabin monitoring** `dms` | `DMS_CAMERA` (default `usb:0`) · re-bindable | child `app/dms_app.py` | loopback **8010** | MediaPipe face fatigue + three-value helmet state, two independently switchable paths |

The "Camera" column is a **default**, not a constraint: each role can be pointed at any detected camera from its
card in the UI, and the choice is persisted (see [Choose a camera per module](#5-choose-a-camera-per-module)). The
defaults come from the protected environment file; a role with no camera simply refuses to start, with a
per-module error, instead of taking the hub down with it.

The hub itself owns **exclusive camera leases** (one holder per physical device), child-process supervision and
restart, USB signal-light arbitration (rear `DANGER` outranks cabin fatigue), thermal de-rating (88 °C step down /
89 °C suspend the helmet path and slow the front view / recover only after staying below 85 °C for 30 s), the
event bus (500-entry in-memory ring + `logs/hub_events.jsonl`), the recording runtime and the MJPEG proxy.

At startup all three roles are **idle**: no camera held, no model loaded, no VRAM allocated. Camera roles never
swap according to discovery order — a role only ever uses the camera the operator bound to it.

## Features

- **Start All / Stop All**: start order is rear → front → cabin; stop order is cabin → front → rear, releasing
  camera, model, CUDA and child process per path. One failing module never tears down a healthy rear view.
- **Per-module camera choice**: each card lists the detected cameras (PoE RTSP, USB nodes, hand-entered sources)
  with reachability and current holder, and the choice is persisted without `sudo` or a restart — see
  [Choose a camera per module](#5-choose-a-camera-per-module).
- **Front-view interaction**: left click to select/refine, right click to exclude, "Clear target" to re-pick.
  After a long loss the target is re-locked only if it passes appearance-template verification, so a different
  object is rejected.
- **Rear warning**: danger distance must be smaller than warning distance (enforced by the UI); the buzzer toggle
  stays in sync with the server; configuration changes take effect immediately and are persisted.
- **Cabin monitoring**: separate fatigue and helmet switches; switching one off really stops that inference
  (no idle spinning, no misleading log lines).
- **Display-level fullscreen**: the fullscreen button on any view, `Esc` or the back icon to return; if the
  browser refuses display-level fullscreen it degrades to an in-page large view, and neither path restarts
  inference.
- **Pure recording centre**: switch from the header; entering it stops all inference first, then you record
  per camera or all at once.
- **Chinese/English toggle**: header switch, browser tab title follows the language
  (`Forklift Vision Control` / `叉车三路视觉总控`).
- **Jetson desktop GUI**: double-click "Visual Hub 总控" on the desktop to open the same pages in a Firefox
  kiosk, with a single-instance lock and on-demand service start.

## Repository layout

```text
seg_demo/
├── app/                camera-side runtimes
│   ├── camera_source.py    unified video sources (usb:/rtsp:/video:/image:/synthetic)
│   ├── web_stream.py       dependency-free MJPEG server shared by rear + cabin
│   ├── warn_app.py         rear-warning entry point (child process)
│   ├── dms_app.py          cabin-monitoring entry point (child process)
│   ├── warning/            rear: depth backends/filters/ROI/obstacles/temporal/risk/render/alarm/retention
│   ├── dms/                cabin: camera/fatigue/helmet/PPE/state/render/web
│   ├── geometry/           camera model, ground plane, pixel→XYZ projection
│   └── depth/              Depth-Anything-V2 metric depth estimation
├── backend/            Visual Hub server
│   └── app/
│       ├── main.py         FastAPI wiring (system/camera/hub/recording/segment)
│       ├── config.py       preview-stream and HTTP defaults
│       ├── api/            system · camera (MJPEG source only) · hub · recording
│       ├── hub/            occupancy leases/child supervision/event bus/thermal/signal light/MJPEG proxy
│       │                   + inventory.py (camera detection, opaque ids, credential redaction)
│       ├── recording/      recording runtime (GStreamer H.264 → MP4)
│       ├── segment/        click-to-segment service + built-in EfficientTAM wrapper
│       ├── camera/         CameraManager (exclusive, state machine)
│       └── streaming/      MJPEG encoder
├── frontend/           React pages: Hub (three-camera console) + Recording (pure recording centre)
├── configs/            dms.yaml · warning.yaml · _runtime_store.py · _runtime_overrides.yaml (runtime)
│                       _camera_bindings.yaml (per-role camera choice; created on demand, git-ignored)
├── deploy/             system-side installation artifacts (see "Installation and deployment")
├── scripts/            1 script, run_visual_hub.sh — the only entry point (see the table below)
├── tests/              single test root: hub/ segment/ dms/ warning/ geometry/ + top-level cases
├── docs/               current documentation; historical material in docs/archive/
├── models/             converted artifacts: onnx/ tensorrt/ mediapipe/ manifests/
├── checkpoints/        upstream weights: EfficientTAM, Depth-Anything-V2, yolov8n
├── third_party/        EfficientTAM upstream checkout (git-ignored, but the venv egg-link points at it — do not delete)
└── logs/               runtime output (see "Logs and retention")
```

**`scripts/` holds exactly one file**

| Script | Subcommands |
| --- | --- |
| `run_visual_hub.sh` | `start` (default — the same script is systemd's `ExecStart`), `stop [--poe]`, `status`, `gui` |

`gui` is the desktop entry: it starts the service when it is down (passwordless `systemctl start --no-block`),
waits for `/api/health`, picks a display the operator can actually see (physical console / RDP / xpra / SSH X11)
and opens the hub full screen in a Firefox kiosk. The display selection is part of this same self-contained file.

Everything else that used to live here — the one-shot environment provisioning, model/engine conversion, camera
calibration, `tegrastats` sampling, the offline segmentation and cabin demos and the README path self-check — was
removed on 2026-09-20: none of it is on the runtime path and the device is already provisioned. Any of those files
can be recovered from this repository's history with `git show HEAD:scripts/<name>` (the `install_dms_models`
helper was never committed; it only exists in the pre-cleanup backup tarball).

## Installation and deployment

### 1. Runtime environment

The runtime environment is already provisioned on this device: `.venv/` carries the Jetson torch/OpenCV runtime
and `third_party/` the editable EfficientTAM checkout. Activate the venv with:

```bash
source .venv/bin/activate
```

The one-shot provisioning helpers were removed on 2026-09-20 (recover with `git show HEAD:scripts/<name>` if the
venv ever has to be rebuilt). They never replaced the Jetson torch runtime with a PyPI wheel and never replaced the
system OpenCV (this device uses the system `python3-opencv 4.5.4` build with GStreamer). The full JetPack 6 → 5
adaptation log lives in `docs/archive/MIGRATION.md`.

### 2. Models and weights

The weights and converted engines are already in place (table below). The one-shot converters that produced them
were removed on 2026-09-20 and can be recovered with `git show HEAD:scripts/<name>`.

| Location | Contents |
| --- | --- |
| `checkpoints/` | upstream weights: `efficienttam_ti_512x512.pt`, `depth_anything_v2_metric_indoor_small/`, `yolov8n.pt` |
| `models/onnx/` | conversion intermediates: depth, person, PPE |
| `models/tensorrt/` | the FP16 engines actually used for inference (**rear and cabin both prefer the engines**) |
| `models/mediapipe/` | `face_landmarker.task` |
| `models/manifests/` | engine provenance and checksum manifests |

### 3. System-side install

The repository can be cloned anywhere; `install.sh` rewrites the reference deployment path
`/home/seeed/workspace/seg_demo` and the user `seeed` to the actual checkout path and `$HUB_USER`:

```bash
git clone https://github.com/zibochen6/rugged_cv_demo.git /home/seeed/workspace/seg_demo
```

> **Path-independent**: `visual-hub.service`, `visual-hub.logrotate` and `visual-hub.desktop` contain the
> reference path and user; `install.sh` rewrites all three at install time, so a clone in a different directory
> (for example `/opt/rugged_cv_demo`) installs correctly.

Idempotent, previewable, and it neither starts nor stops services nor installs packages:

```bash
sudo ./deploy/install.sh --dry-run     # preview
sudo ./deploy/install.sh               # install / refresh
```

| Installed to | Source | Notes |
| --- | --- | --- |
| `/etc/systemd/system/visual-hub.service` | `deploy/visual-hub.service` | `User=seeed`, `KillMode=control-group`, `TimeoutStopSec=45`; `EnvironmentFile=-…` (optional on purpose); repo path rewritten to this checkout |
| `/etc/systemd/system/poe-pse.service` | `deploy/poe-pse.service` | PoE PSE power hold (carries the ordering-cycle note — do **not** add `After=multi-user.target`) |
| `/etc/systemd/system/poe-cam-net.service` | `deploy/poe-cam-net.service` | camera NICs and subnets, a resident `Type=simple` link watcher that re-provisions whenever a PoE port changes state |
| `/usr/local/bin/poe-cam-up.sh` | `deploy/poe-cam-up.sh` | the script the watcher above runs with `--loop`; a bare run is one provisioning pass |
| `/etc/logrotate.d/visual-hub` | `deploy/visual-hub.logrotate` | daily / 20 MB rotation with compression; log directory rewritten to this checkout |
| `/etc/sudoers.d/seeed-nopasswd` | `deploy/seeed-nopasswd.sudoers` | validated with `visudo -c -f` first; the drop-in is removed automatically if the whole config then fails to parse |
| `/etc/seg-demo/visual-hub.env` | `deploy/visual-hub.env.example` | generated from the template **only when missing**, never overwritten afterwards |
| `~/Desktop/visual-hub.desktop` | `deploy/visual-hub.desktop` | `Exec` rewritten to the real repo path and marked trusted |

### 4. Protected environment file

Real RTSP credentials live only in `/etc/seg-demo/visual-hub.env` (`root:root 0600`). **These values are factory
defaults, not requirements**: the unit uses `EnvironmentFile=-…`, so the hub boots with no such file at all, and
each role can be re-bound from the UI afterwards.

| Variable | Required | Meaning |
| --- | --- | --- |
| `FRONT_CAMERA_URL` | — | front camera RTSP URL (no default; the role stays unconfigured until set) |
| `REAR_CAMERA_URL` | — | rear camera RTSP URL (no default; `RTSP_URL` still works as a legacy alias) |
| `DMS_CAMERA` | — | cabin camera, defaults to `usb:0`; any `usb:<idx>` / `rtsp://…` / `video:<file>` / `image:<path>` / `synthetic` is accepted |
| `HUB_EXTRA_CAMERAS` | — | comma-separated spare camera URLs offered in the picker without promoting them to a role default |
| `HUB_CAMERA_BINDINGS` | — | path of the per-role binding file, defaults to `configs/_camera_bindings.yaml` |
| `HUB_PORT` | — | defaults to `8000` |
| `SEG_DEMO_RUNTIME_OVERRIDES` | — | rear-config persistence path, defaults to `configs/_runtime_overrides.yaml` |
| `VISUAL_HUB_RECORDING_ROOT` | — | recording root, defaults to `~/Videos/visual-hub` |
| `FRONT_PORT` / `WEB_PORT` / `DMS_PORT` / `HUB_PYTHON` | — | port and interpreter overrides (normally untouched) |

The web UI, the public status API, the logs and the child-process command lines only ever show an opaque camera id
plus a credential-free label such as `RTSP · 192.168.137.20` — never the full URL. `tests/hub/test_runtime.py`
asserts that no status payload contains `rtsp://` at all.

### 5. Choose a camera per module

Every module card carries a **Camera** selector listing the cameras the hub can actually see:

| Kind | Detected from | Reachability shown as |
| --- | --- | --- |
| `rtsp` | `FRONT_CAMERA_URL` / `REAR_CAMERA_URL` / `HUB_EXTRA_CAMERAS` | a bounded TCP connect to `host:554` (cached 10 s) |
| `usb` | `/sys/class/video4linux` → `usb:<idx>` | whether `/dev/video<idx>` exists |
| `file` | typed by hand (`video:<path>` / `image:<path>`) | whether the path exists |
| `test` | `synthetic` (cabin only) | always available |

What the selector does:

- **Detection never opens a device.** `backend/app/hub/inventory.py` reads sysfs only. Opening a camera that a
  module already holds fails at best and steals the handle at worst, so the older `enumerate_devices()` helper
  (which does open every node) is deliberately not used.
- **Credentials never leave the device.** The API addresses cameras by an opaque `sha256(source)[:12]` id and
  returns a redacted `source`. Typing an RTSP URL by hand goes through `POST /api/hub/cameras/probe`, which
  classifies and redacts it without persisting anything.
- **USB cameras: one streaming node per camera, and one bus between them.** A UVC camera registers several
  V4L2 nodes on one USB interface — the streaming function (`index` 0) and a metadata function (`index` 1) that
  cannot be opened at all — so only the streaming node is listed (`USB · /dev/video2 (1080P USB Camera @1-2.1)`).
  The `@1-2.1` part is the USB port path, and it is the *only* stable way to tell two identical cameras apart:
  cheap UVC models report the same product, vendor, and even the same serial string. `/dev/video2` and `usb:2`
  are the same camera written two ways; both are accepted and always normalized to `usb:<idx>`.
- **Two USB cameras on one USB 2.0 bus cannot stream at the same time.** On the Rugged J401 both cameras land on
  the same `usb1` hub (480 Mbps), and the kernel refuses the second one with `Not enough bandwidth for
  altsetting 1` — at every resolution, even 320×240. Move one camera to the USB 3 socket (`usb2`, 10 Gbps) or
  run only one. The module reports the failure (`degraded`, `camera_running: false`, `last_error` set) rather
  than showing a green indicator at 0.0 FPS.
- **The API ships no display prose.** `/api/hub/cameras` returns structured fields plus language-neutral
  source-scheme tokens (`RTSP · 192.168.137.20`, `USB · /dev/video0`, `video · clip.mp4`). Every human-readable
  word the operator sees is composed by the UI from its own translations, so an English interface stays English;
  a test asserts the whole payload contains no CJK characters at all.
- **Switching a running module stops and restarts it.** The source is handed to the child process (or to the
  in-process `CameraManager`) once, at start; neither `/api/config` nor `/api/dms_config` can change it live. The
  UI confirms before doing this.
- **One camera, one holder.** A camera already leased by another module is shown greyed out with its holder, and
  a forced request is refused with `409 CAMERA_BUSY`. Recording mode refuses a rebind outright
  (`409 RECORDING_ACTIVE`), because recording holds its own capture handles on the same sources.
- **The choice persists.** It is written to `configs/_camera_bindings.yaml` (mode `0600`, git-ignored) by the
  `seeed` user, so no `sudo` and no service restart is involved. Precedence is
  **binding file → environment → built-in default**, and only a value that *differs* from the environment default
  counts as an override, so re-selecting the factory camera clears the "recalibrate" warnings.
- **Deleting that file restores the previous behaviour exactly.** It is the whole rollback.

Two consequences worth knowing:

- **`usb:<idx>` indexes are not stable across replugging.** Ids are hashes of the source string, so a camera that
  re-enumerates at a different index appears as a new entry; re-select it (or type the source) if that happens.
- **Switching cameras invalidates calibration.** The rear depth thresholds are calibrated against the factory PoE
  camera (`configs/warning.yaml` `camera.intrinsics` / `camera_mount`), and the front view's geometry is not
  calibrated for a different sensor. The card shows a warning whenever a role is bound to a non-default camera.

Something that is *not* detected: a brand-new PoE camera on an unknown subnet. `deploy/poe-cam-up.sh` already owns
subnet provisioning for the configured subnets; to use a camera it has not been told about, add it to
`HUB_EXTRA_CAMERAS` or type its URL in the picker.

## Running and operations

### Start / stop / status

```bash
sudo systemctl start|stop|restart visual-hub
sudo systemctl status visual-hub
journalctl -u visual-hub -n 200 --no-pager

./scripts/run_visual_hub.sh            # foreground start (systemd ExecStart uses the same script)
./scripts/run_visual_hub.sh stop       # stop recording -> all three modules -> the hub -> verify port/children released
./scripts/run_visual_hub.sh stop --poe # additionally cut PoE power and the subnet provisioning
./scripts/run_visual_hub.sh status     # read-only status
```

`stop` is idempotent and passwordless: running it again returns 0 with an "already stopped" message. The main
path is `POST /api/recording/actions/stop-all` → `POST /api/hub/actions/stop-all` → `systemctl stop`, and only if
that fails does it fall back to `SIGTERM` on the hub process. `--poe` cuts camera power; `poe-cam-net` is a
resident link watcher, so the camera subnets are re-provisioned as soon as a PoE port has link again — there is
no fixed probe window to wait out.

Two things to know before running the script by hand:

- **A bare `./scripts/run_visual_hub.sh` fails with exit 3 if the port is already taken**, which means the service
  is already up. It says so instead of leaving you with uvicorn's `[Errno 98] address already in use`. To restart,
  use `sudo systemctl restart visual-hub`; to run in the foreground, `./scripts/run_visual_hub.sh stop` first.
- **A manual run cannot see `FRONT_CAMERA_URL` / `REAR_CAMERA_URL`.** `/etc/seg-demo/visual-hub.env` is
  `root:root 0600` and unreadable by `seeed`, so only systemd can inject it. A foreground run therefore falls back
  to `configs/_camera_bindings.yaml` (which the web UI writes, and which *is* readable by `seeed`) and otherwise
  starts with no cameras at all — it prints exactly that, rather than looking like the configuration was lost.

### Auto-start at boot

All three units are `enabled`. **Note:** `poe-pse.service` used to contain `After=multi-user.target` while being
`WantedBy=multi-user.target`, which forms an ordering cycle
(`multi-user.target → visual-hub → poe-pse → multi-user.target`). systemd resolves that by **deleting the boot
start jobs of `visual-hub` and `poe-cam-net`** — in other words, neither ever auto-started. That line is gone.

Measured after a real reboot (2026-09-19 15:01): **0** `ordering cycle` lines in the boot journal,
`visual-hub` / `poe-pse` / `poe-cam-net` all started automatically, `/api/health` healthy, all three roles idle.
`deploy/install.sh` checks at install time whether that line has crept back in.

### Jetson desktop GUI

With a monitor attached, double-clicking "Visual Hub 总控" on the desktop starts the service without a password
prompt and opens the same pages in a Firefox kiosk. A single-instance lock makes repeated clicks just raise a
notification; when the service is down the entry starts it with
`sudo -n systemctl start --no-block` and then polls `/api/health` (≤300 s), so a slow service start (model load,
camera open) cannot freeze the desktop entry. To run it by hand:

```bash
./scripts/run_visual_hub.sh gui
```

### Day-to-day operation

- Closing the browser does not stop the safety functions; releasing resources requires an explicit "Stop All"
  or `stop`.
- Rear-view configuration changes take effect immediately and are written to `configs/_runtime_overrides.yaml`
  (`configs/warning.yaml` is never overwritten).
- On a single-path failure, check `last_error` in the status card and `logs/hub_<module>.log` first.

## Configuration

### Rear view configuration

`configs/warning.yaml`, top-level sections: `pipeline` `system` `camera` (including `intrinsics` / `calibrated`)
`model` `depth` `camera_mount` `danger_roi` `collision_corridor` `ground_filter` `obstacle` `distance` `temporal`
`ttc` `velocity` `warning` (danger/warning distances and hysteresis) `person` (person channel and distance
thresholds) `watchdog` `logger` `events` `io` (alarm mode).

- State machine `SAFE → WARNING → DANGER` with hysteresis on both transitions; obstacles need several consecutive
  frames; distance is the 10th percentile over the obstacle region.
- Camera loss, consecutive invalid depth frames, or a failing inference backend → `SYSTEM ERROR` (fail-visible).
- With `camera.calibrated: false` the intrinsics fall back to a 60° horizontal FOV estimate, so distances only
  carry ordinal meaning; a real calibration (checkerboard intrinsics, produced once by a helper that was removed
  on 2026-09-20) has to be copied into the config by hand.
- Details: [docs/warning.md](docs/warning.md).

### Cabin configuration

`configs/dms.yaml`, top-level sections: `system` `camera` `runtime` `fatigue` `helmet` `web` `ui` `logger`
`notices`. Fatigue and helmet can be switched independently; when one is off its model is not loaded and no
misleading log lines are written. Details (including the honest wording of what the heuristics do and do not
claim): [docs/dms_helmet_demo.md](docs/dms_helmet_demo.md).

### Runtime overrides

`configs/_runtime_overrides.yaml` is written by the web UI (`_runtime_store.py` replaces it atomically) and holds
only user-facing switches, for example:

```yaml
runtime:
  danger_m: 1.3
  warning_m: 1.7
  buzzer: true
```

`configs/_camera_bindings.yaml` is the one other runtime file, written by the same store with the same atomic
write, but as a separate file so the hub never read-modify-writes the file a child module is also updating:

```yaml
cameras:
  dms: usb:0
```

It holds a raw RTSP URL when a role points at a PoE camera, so it is created `0600` and git-ignored. Remove it to
fall back to the environment defaults.

## Recording centre

Switch to **Recording Center** in the header: entering it stops all inference first, then you record per camera or
all at once. Only raw frames are stored — no models loaded, no overlay, no audio.

- Pipeline: GStreamer `nvv4l2h264enc` → H.264 MP4, **1920×1080 @ 15 FPS**, up to **30 minutes** per segment.
- Storage: `~/Videos/visual-hub/<camera>/<date>/` (point `VISUAL_HUB_RECORDING_ROOT` at a larger disk if needed).
- Live preview and fullscreen are available while recording; closing the page does not stop recording. Files are
  written under a temporary `.part.mp4` name, renamed on a clean stop, and leftovers from an abnormal stop are
  reconciled on the next start.
- Recording and inference are mutually exclusive: press "Stop all recordings", then "Return to Live Inference".
- Service shutdown closes every recording file and releases both PoE streams and `/dev/video0`.

## HTTP API

`{module_id}` ∈ `front|rear|dms`; `{camera}` is the same set. Status payloads use Chinese module labels as
`label` values; the frontend localises them through `src/i18n.ts`.

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/health` | health check (used by the frontend and the desktop entry to detect a usable service) |
| GET | `/api/system/info` `/api/system/openapi` | environment and OpenCV capabilities |
| GET | `/api/hub/status` | full hub snapshot: per-module state/metrics, occupancy, events, thermal policy, recording, signal light |
| GET | `/api/hub/events` | event bus |
| GET | `/api/hub/modules/{module_id}` | one module's status |
| POST | `/api/hub/modules/{module_id}/start` `stop` `restart` | per-module start/stop/restart |
| POST | `/api/hub/actions/start-all` `stop-all` | Start All / Stop All |
| POST | `/api/hub/modules/{rear,dms}/config` | rear distances and buzzer, cabin switches |
| GET | `/api/hub/cameras` | detected cameras (opaque id + redacted source) and the current per-role binding |
| POST | `/api/hub/cameras/probe` | classify and redact a hand-entered source; persists nothing |
| PUT | `/api/hub/modules/{module_id}/camera` | bind a role to `{"camera_id": …}` or `{"source": …}`; `?restart=false` applies at next start |
| GET | `/api/hub/stream/{module_id}` | the three MJPEG streams (the browser's only video entry point) |
| GET | `/api/camera/stream.mjpg` | raw front MJPEG (the source behind the front stream above) |
| GET | `/api/segment/status` | front segmentation status |
| POST | `/api/segment/start` `stop` `click` `clear` | segmentation start/stop, positive/negative point, clear target |
| GET | `/api/recording/status` | recording mode, per-camera state, storage headroom |
| POST | `/api/recording/mode/enter` `exit` | enter/leave recording mode |
| POST | `/api/recording/cameras/{camera}/start` `stop` | per-camera recording |
| POST | `/api/recording/actions/start-all` `stop-all` | record all / stop all recordings |
| GET | `/api/recording/stream/{camera}` | recording preview |
| GET | `/api/recording/files` | recorded files |
| GET | `/api/recording/files/{id}/play` `download` | play / download |

Rear-view configuration example: `{"danger_m": 1.5, "warning_m": 3.0, "buzzer": false}`

## Logs and retention

`logs/` holds runtime output only, with three retention layers:

| File | Writer | Retention |
| --- | --- | --- |
| `danger_*.jpg` | rear `DANGER` snapshots | pruned in code: newest 200 and ≤14 days |
| `session_*.csv` | rear per-frame telemetry (one file per run) | pruned in code: newest 40 and ≤14 days |
| `hub_*.log`, `hub_events.jsonl`, `dms_events.jsonl` | hub / child processes | logrotate: daily or 20 MB, 7 compressed generations |
| `tegrastats.log` | manual `tegrastats` sampling | managed by hand |

Without `logrotate` the last two groups grow without bound. After installing it, confirm:

```bash
command -v logrotate || sudo apt-get install -y logrotate
sudo ./deploy/install.sh
systemctl list-timers | grep logrotate
```

## Tests

```bash
.venv/bin/python -m pytest -q          # single test root tests/ (pytest.ini is already configured)
cd frontend && npm run build
```

Currently **343 passed, 2 skipped**. Every case that needs real hardware is gated behind
`SEG_DEMO_HARDWARE_TESTS=1` (skipped by default, visible rather than hidden):

```bash
SEG_DEMO_HARDWARE_TESTS=1 .venv/bin/python -m pytest -q tests/test_camera_manager.py \
    tests/segment/test_model_load.py
```

The README path self-check that used to guard this section (72 references, 0 unresolved when it was last run) was
removed on 2026-09-20 with the rest of the one-shot helpers; recover it with `git show HEAD:scripts/<name>` if the
docs are reworked again.

| Directory | Coverage |
| --- | --- |
| `tests/hub/` | runtime supervision, recording runtime, thermal policy, signal light, cabin events, camera inventory/redaction and per-role camera binding (including "no status payload contains an RTSP URL") |
| `tests/segment/` | segmentation service/state machine/polygons/templates/model load (hardware-gated) |
| `tests/dms/` | fatigue signals and state machine, helmet heuristic, PPE, render/HUD, switches, web contract |
| `tests/warning/` | risk core, synthetic-scene gate, person channel, thermal de-rating, retention pruning |
| `tests/geometry/` | camera model and pixel→XYZ projection |
| top level | `CameraManager` lifecycle, warning core, synthetic scenes |

## Troubleshooting

| Symptom | Check | Action |
| --- | --- | --- |
| Double-clicking the desktop icon asks for a password | does `/etc/sudoers.d/seeed-nopasswd` exist | `sudo ./deploy/install.sh` |
| Port 8000 unreachable after boot, service inactive | `journalctl -b \| grep "ordering cycle"`, `systemctl is-enabled visual-hub` | `sudo ./deploy/install.sh` (it checks and reports the cycle) |
| A module reports "camera busy" | `occupancy` in `curl -s :8000/api/hub/status` | stop the current holder; for USB use `fuser -v /dev/video0` |
| A camera is missing from the picker | `curl -s :8000/api/hub/cameras` | the list is "detected + currently bound": add the URL to `HUB_EXTRA_CAMERAS`, or type it in the picker's manual row |
| A per-role camera choice will not stick | `cat configs/_camera_bindings.yaml`, `ls -ld configs/` | the file must be writable by `seeed`; point `HUB_CAMERA_BINDINGS` at a writable path if the checkout is not |
| A USB camera is listed but a module cannot open it | `sudo dmesg \| grep -i "not enough bandwidth"` | two UVC cameras on one USB 2.0 bus: move one to the USB 3 socket (`usb2`) or stop the other module |
| Picker shows a camera as "not responding" | `ping -I eth1 <camera IP>`, `curl -s :8000/api/hub/cameras` | the reachability column is a real TCP connect to `host:554`; a dead PoE camera is reported rather than hidden |
| Front view has video, rear/cabin do not | `ss -ltnp \| grep -E ':8080\|:8010'` | 8080/8010 are loopback-only; the browser must use `/api/hub/stream/*` |
| Front view takes ~8 s to appear | the model-load line in `journalctl -u visual-hub` | first load of the EfficientTAM weights — expected |
| Both PoE cameras drop or keep restarting | shared PoE power budget | move at least one camera to an external PoE switch/injector (infinite RTSP retries cannot fix a brownout) |
| Front view stuck on "Connecting to camera…" with CAPTURE 0.0 FPS | `ip -br addr show eth0 eth1 eth2 eth3 eth4`, `ip route get 192.168.137.20`, `journalctl -u poe-cam-net -n 40` | the PoE port holding the camera must also hold the `192.168.137.100/24` address. `poe-cam-net` assigns it as soon as a port has link, so an empty port means no link (cabling/PSE), not a subnet bug |
| PoE camera pings, but the front view is frozen (fps 0, frame age growing) | `frame_age_s` in `curl -s :8000/api/hub/modules/front` | the capture supervisor re-opens a stalled RTSP stream after `SEG_DEMO_RTSP_STALL_TIMEOUT_S` (8 s); repeated "RTSP reconnect failed" means the camera itself is not answering RTSP |
| `*_events.jsonl` keeps growing | `systemctl list-timers \| grep logrotate` | install logrotate and re-run `deploy/install.sh` |
| `signal_light.last_error` shows a serial `FileNotFoundError` | is a serial signal light attached? | expected when none is; adjust `configs/warning.yaml: io.alarm_mode` |
| Cabin view unusable at night or in strong backlight | is there a near-infrared camera? | without NIR the RGB face landmarks fail — a known limitation |
| Camera still held after stopping | output of `./scripts/run_visual_hub.sh stop`, `pgrep -af 'warn_app\|dms_app'` | wait 45 s (`TimeoutStopSec`); if anything is left, check the journal |

```bash
curl -s http://127.0.0.1:8000/api/hub/status          # hub and per-module status
ss -ltnp | grep -E ':8000|:8010|:8080'                # only 8000 should be public
fuser -v /dev/video0                                  # no holder after the USB path stops
ps -ef | grep -E 'warn_app|dms_app|backend.app.main'
tegrastats --interval 5000 >> logs/tegrastats.log     # manual sampling
```

## Security

**Credentials.** Real RTSP credentials live only in `/etc/seg-demo/visual-hub.env` (`root:root 0600`); the template
is `deploy/visual-hub.env.example`. Never commit real credentials, and never write `SUDO_PASS=...` or
`echo <password> | sudo -S` into a script.

**Passwordless sudo.** This device installs `deploy/seeed-nopasswd.sudoers` as
`/etc/sudoers.d/seeed-nopasswd`, giving `seeed` **full passwordless sudo** (enabled at the operator's request so
the desktop icon needs no password).

```bash
sudo rm -f /etc/sudoers.d/seeed-nopasswd      # uninstall (the GUI falls back to a pkexec prompt)
```

Implication: any process running as `seeed` can obtain root without a password. Keep the device on a trusted LAN
and control physical access. If the sudoers drop-in is ever corrupted, recover through a serial/single-user
console or `pkexec visudo`.

**Perception boundaries (stated honestly):**

- **Rear warning.** Monocular RGB learned metric depth, which fails in low light, on strong reflections,
  transparent or black objects, with a dirty lens, and under heavy vibration. It is a driving aid only;
  unreliable states must show `SYSTEM ERROR` and never default to SAFE.
- **Cabin monitoring.** Fatigue = MediaPipe Face Landmarker plus fixed time rules, with **no** per-driver PERCLOS
  calibration; helmet = a colour/skin/dark-region heuristic over the head ROI of a person box, reported as one of
  three values (worn / not worn / unknown), never as a percentage. Both are **demo-grade**, hold no safety
  certification and must not be used for compliance decisions.
- **Front segmentation.** An interactive tracking tool (click to select, negative points to exclude, re-lock on
  reappearance), not automatic object recognition, and it produces no safety conclusions.

## Backup and restore

Before any large-scale change, a recovery package is kept outside the project:

```text
/home/seeed/workspace/.seg_demo_backups/<timestamp>/
```

It contains the Git state, a tracked-worktree patch, a tarball of untracked sources, per-phase deletion lists,
before/after reports and `SHA256SUMS`. Stop `visual-hub` before restoring and follow the manifests; do **not** use
a hard reset that overwrites the current working tree.

## Documentation

> The in-depth documents below are currently Chinese-only; this English README is the entry point, and the
> Chinese manual is [README.zh.md](README.zh.md).

Current:

| Document | Contents |
| --- | --- |
| [docs/warning.md](docs/warning.md) | rear depth warning: state machine, performance, calibration, tests |
| [docs/dms_helmet_demo.md](docs/dms_helmet_demo.md) | cabin fatigue/helmet: contract, switch semantics, honest wording, acceptance |
| [docs/click-segment.md](docs/click-segment.md) | front click-to-segment: interface and usage |
| [docs/SEGMENT-LESSONS-LEARNED.md](docs/SEGMENT-LESSONS-LEARNED.md) | re-appearance/re-lock troubleshooting and checklist |
| [docs/requirements/visual-hub-gui-fullscreen/](docs/requirements/visual-hub-gui-fullscreen/requirements-contract.md) | desktop GUI and display-level fullscreen requirement contract (frozen) |
| [docs/forklift_scenario_research/](docs/forklift_scenario_research/README.md) | scenario research (historical snapshot, paths mostly predate the current layout) |

Historical material (calibration studio, 6D pose/marker, 3D click tracking, dataset tooling, cargo placement,
machine migration) is in `docs/archive/`; most of its paths and commands no longer work and it is kept for
provenance only.

## Known limitations

- Front segmentation takes ~7–8 s on its first load; it tracks a single target — multi-target use requires
  clearing and re-selecting.
- Cabin fatigue has no per-driver calibration and no near-infrared capability; the helmet state is a three-value
  heuristic.
- The rear and front cameras share the Rugged J401 PoE power budget; when both drop at once, suspect power
  before software.
- `poe-cam-net.service` is a resident link watcher, not a boot-time oneshot: a PoE camera whose link only comes
  up minutes after boot, or after the cable is moved to another PoE port, is still provisioned. The hub stays
  usable meanwhile (the dependency was decoupled so it no longer queues behind provisioning).
- The front (PoE RTSP) capture supervises itself: OpenCV's GStreamer `read()` blocks for as long as the source is
  silent, so a stalled stream is detected by frame age and the stream is re-opened in place
  (`SEG_DEMO_RTSP_STALL_TIMEOUT_S`, default 8 s; `SEG_DEMO_RTSP_RECONNECT_MIN_INTERVAL_S`, default 2 s).
- The published repository is a single initial commit; the device's own Git working tree still carries its
  uncommitted local state, so decide the ongoing commit strategy separately.