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
