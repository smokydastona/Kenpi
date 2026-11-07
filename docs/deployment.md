# Deployment readiness

The local read-only status service can be started with
`kenpi --config config/kenpi.example.toml`. Supervised installation and rollback
resources now exist for this **status service only**. Raspberry Pi/vehicle
deployment has not been physically validated, and this is not an installation
of a functioning dashcam gateway.

The server binds to loopback and exposes only health, capability, and static UI
routes. Do not enable a hardware service based solely on the interfaces in this
package. Installation never enables GPIO, UART, camera access, or camera power
control.

## Clean installation

Use a supported Linux system with Python 3.11+, `python3-venv`, Git, Bash,
systemd, curl, and util-linux (`flock`/`runuser`). Obtain a trusted committed
checkout and run these commands from its root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/ruff format --check kenpi tests
.venv/bin/ruff check kenpi tests
.venv/bin/python -m unittest discover -v
sudo bash "$PWD/deploy/install-status.sh" "$(git rev-parse HEAD)"
systemctl status kenpi-status.service
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/api/v1/capabilities
```

The installer archives the exact full commit into `/opt/kenpi/releases/`,
builds as the unprivileged `kenpi` account, pins setuptools 78.1.1 and installs
the local standard-library-only package without additional runtime dependencies.
It then removes source/build scratch and makes the release root-owned and
non-writable by the service. It refuses unexpected accounts, symlinks, dependency
manifests, and modified existing status configuration rather than guessing.

First-run configuration is `/etc/kenpi/status.toml`, owned by `root:kenpi` with
mode `0640` inside a `0750` directory. It contains actual safe loopback settings,
not credentials. The service runs as `kenpi`, has no capabilities, private device
and temporary namespaces, read-only filesystem protection, and network access
limited to localhost. It restarts on failure with bounded start attempts. Its
stdout/stderr and sanitized application logs go to the journal.

## Upgrade and rollback

Install another trusted full commit using the same command. Deployment locking
prevents overlapping upgrades. A release switch is atomic, existing unit/config
files are backed up, and failed activation restores the previous installation.
The selected commit's unit and configuration are staged in protected root-owned
storage before unprivileged build hooks run; root never installs deployment
metadata from the builder-writable source tree. Activation probes have connection,
request, and retry-time bounds.
Existing release directories are retained for rollback:

```sh
readlink /opt/kenpi/current
ls /opt/kenpi/releases
sudo bash "$PWD/deploy/rollback-status.sh" FULL_PREVIOUS_COMMIT_SHA
```

Replace the final argument with a real previously installed 40-character commit
ID. Rollback checks ownership and permissions and tests health after activation.
Do not edit installed release files or run application builds as root.

## Operation and troubleshooting

```sh
journalctl -u kenpi-status.service --since today
systemctl show kenpi-status.service -p ActiveState -p SubState -p NRestarts
sudo systemctl restart kenpi-status.service
sudo systemctl stop kenpi-status.service
systemd-analyze verify "$PWD/deploy/kenpi-status.service"
```

Unit verification before installation may report the expected absent
`/opt/kenpi/current/venv/bin/python`. Physical activation, cgroup network
restriction support, reboot/restart behavior, and Pi thermals still require
validation on the target OS. Installer source, shell checks, unit validation,
and a built wheel do not prove an installed hardware service.

Health distinguishes unavailable measurements, unsupported platform evidence,
and partial collection through `collection_status`/`collection_errors`.
Missing Linux metrics are not camera errors. Connectivity failures clear
dashboard values rather than leaving an old successful snapshot.

The Android companion additionally needs a separately provisioned private HTTPS
gateway; the installer does not expose the unauthenticated API to Wi-Fi. Camera
credentials, protocol drivers, preview, ESP32 power coordination, and physical
Kenwood integration are not supplied by this status installer.

## Pi OS image (optional)

An optional reproducible pi-gen based build that preinstalls and enables this
same status service on a fresh Raspberry Pi OS Lite (64-bit) image is in
`os-image/`. It changes nothing about what the service does; see
`os-image/README.md` for exact scope, safe defaults, and build instructions.

## Release bundle (CI)

`.github/workflows/bundle.yml` runs on every push and uploads one artifact,
`kenpi-<version>`, which downloads as a single zip containing:

| Folder | Contents |
|---|---|
| `python/` | kenpi wheel |
| `firmware/` | ESP32-S3 controller binaries (`pio run -e esp32s3`) |
| `android/` | companion APK (debug-signed; not a Play Store release) |
| `os-image/` | Raspberry Pi OS image built with pi-gen |
| `deploy/` | install/rollback scripts, systemd unit, example config |
| `manifest.json`, `SHA256SUMS` | versions, commit, per-file checksums |

The version is `VERSION` (MAJOR.MINOR, edit to bump a release line) plus the
commit count, e.g. `0.2.57`, so every commit gets a higher number with no manual
edits. The same number is stamped into the wheel and the APK (`versionName`,
`versionCode` = commit count). The OS image installs the committed snapshot, so
its embedded Python package reports the version in `pyproject.toml`, not the
stamped one.

If any component fails to build, the manifest marks it `missing`, the bundle
job fails, and a partial bundle is still uploaded for inspection. Status: this
workflow has not yet been run on GitHub; the ESP32-S3 build and the pi-gen image
build in particular have never completed in this project's environment.
Retention is 90 days. The bundle contains build outputs only; nothing in it is
hardware-verified.

## Safe mode

`kenpi --safe` (or creating `/var/lib/kenpi/safe-mode-once`, which is consumed
on the next start so a crash loop cannot persist it) starts only the loopback
status API. The vehicle UART link and the local media library are not started,
and `/api/v1/diagnostics` reports `"safe_mode": true`. Use it to recover when a
serial device or the media directory prevents normal startup. It never touches
the dashcam, which records independently. The idea is inspired by OctoPrint's
safe mode; no OctoPrint or OctoPi code was used (AGPL/GPL).

## Gateway, pairing, remote access and backups (ADR 0001-0004)

All of this is off by default; the loopback status API is unchanged.

Gateway (`[kenpi]` keys): `gateway_enabled = true`, `gateway_hosts = ["192.168.x.y"]` (explicit
private addresses, never wildcard/public), `gateway_port = 8443`, `state_directory`,
`wireguard_network`, `wireguard_port`. Install `deploy/kenpi-gateway.conf` as a systemd drop-in
(`kenpi-status.service.d/gateway.conf`) so the service may bind private addresses. State lives in
`<state_directory>/gateway/` (0600 files; credentials are stored only as keyed hashes).

Pairing: `kenpi-devices pair --name "Phone"` prints a single-use, 5-minute `kenpi://pair` payload
(QR if `qrencode` is installed); after the device enrols, `kenpi-devices pending` then
`approve <id>`. `list`, `revoke`, `reenroll`, `allow-remote`/`deny-remote` manage devices.

Remote: `kenpi-devices remote init-key|enable|render|apply|disable`. Requires `wireguard-tools`;
a device also needs `allow-remote`. WireGuard never replaces Kenpi authentication.

Backups: copy `deploy/backup.env.example` to `/etc/kenpi/backup.env`, install
`deploy/kenpi-backup.{service,timer}`, then `systemctl enable --now kenpi-backup.timer`. Manual:
`python -m kenpi.tools.backup --dest local:/media/usb create|list|verify|prune|restore`. Restore is
a dry run unless `--confirm`; credentials are never restored, and paired devices are revoked after a
restore so they must re-enrol.
