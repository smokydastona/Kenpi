# Kenpi OS image

An optional, reproducible Raspberry Pi OS (64-bit) Lite image with the kenpi
read-only status service preinstalled and enabled. This is a convenience for
flashing a fresh SD card; it changes nothing about what the service does or is
allowed to do. **Status: built from this pipeline, not yet booted on physical
hardware by this project** (see `docs/hardware-standard.md`, `docs/deployment.md`).

## What this does and does not do

- Uses the official [`RPi-Distro/pi-gen`](https://github.com/RPi-Distro/pi-gen)
  tool, pinned to a specific commit, run inside its own Docker build (pi-gen is
  not vendored or forked into this repository).
- Builds through pi-gen's stage2 ("Lite"), then adds one extra stage
  (`stage-kenpi`) that installs a pinned, committed snapshot of this
  repository's `kenpi` package into a venv, installs the same
  `deploy/kenpi-status.service` unit and `deploy/status.toml` config used by
  `deploy/install-status.sh`, and runs `systemctl enable` (not `--now`) so the
  service starts on first real boot.
- Does **not** enable the camera, GPIO, UART, or any vehicle integration.
  `deploy/status.toml` ships with `camera_enabled = false` and
  `vehicle_uart_enabled = false`, matching `kenpi/core/config.py`'s rejection
  of those flags. The service still binds loopback-only.
- Does **not** bake in SSH, a password, or Wi-Fi credentials unless you
  explicitly opt in via environment variables (below). Leaving `FIRST_USER_PASS`
  unset means Raspberry Pi OS's own first-boot setup wizard asks for a password
  instead of shipping a default one.
- Exports exactly one image (`kenpi-kenpi.img`), not the extra plain Lite image
  pi-gen can also produce.

## Requirements

Docker, and everything `pi-gen`'s Docker build needs on the host (see the
[pi-gen README](https://github.com/RPi-Distro/pi-gen#docker-build)): loop
device and binfmt support for ARM64 emulation. This is a multi-gigabyte
download-and-build; expect it to take a long time and disk space.

## Build

From a clean, committed checkout (uncommitted changes are not included; the
pipeline exports the selected commit with `git archive`, not the working tree):

```sh
os-image/build.sh                 # builds HEAD
os-image/build.sh <full-commit-sha>
```

Optional environment variables, all unset/safe by default:

| Variable | Default | Effect |
|---|---|---|
| `KENPI_OS_ENABLE_SSH` | `0` | Set to `1` to enable the SSH server |
| `KENPI_OS_FIRST_USER_PASS` | unset | Bakes in a password instead of the first-boot wizard; avoid for anything but local testing |
| `KENPI_OS_HOSTNAME` | `kenpi` | mDNS/hostname |
| `KENPI_OS_LOCALE` | `en_US.UTF-8` | |
| `KENPI_OS_TIMEZONE` | `Etc/UTC` | |
| `KENPI_OS_RELEASE` | `trixie` | Release pi-gen builds; must match the pinned pi-gen arm64 branch (its stage0 warns on mismatch, and a bookworm build failed on missing signing keys in CI) |
| `KENPI_OS_USER` | `pi` | First user account name |
| `KENPI_OS_WORK_DIR` | `os-image/.pi-gen` | Where pi-gen is cloned/built; not committed |

The finished image is under `os-image/.pi-gen/deploy/`.

## After first boot

Flashing and booting this image is not, by itself, hardware validation: it
only confirms the status service starts. Follow `docs/deployment.md`'s
verification steps (`systemctl status kenpi-status.service`,
`curl --fail http://127.0.0.1:8080/healthz`, `/api/v1/capabilities`) on the
actual device, and keep treating every camera/vehicle capability as
unverified until it clears the gates in `docs/evidence-and-validation.md`.

## Updating the pi-gen pin

Bump `PI_GEN_COMMIT` in `build.sh` deliberately, and re-check `stage2`'s
package list and defaults for anything that would change what this image
enables by default.
