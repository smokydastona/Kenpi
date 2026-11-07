#!/bin/bash -e
# Runs inside the target chroot (under qemu emulation for arm64). Builds the
# kenpi package from the pinned source already copied in by 01-run.sh, with
# the same validation the host deployment script (deploy/install-status.sh)
# performs, and enables (but does not start) the read-only status service.
# No camera, GPIO, UART, or non-loopback network access is enabled here.

commit=$(cat /opt/kenpi/KENPI_COMMIT)
release="/opt/kenpi/releases/$commit"

if ! getent group kenpi >/dev/null; then
	groupadd --system kenpi
fi
if ! getent passwd kenpi >/dev/null; then
	useradd --system --gid kenpi --home-dir /nonexistent \
		--shell /usr/sbin/nologin kenpi
fi

python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
python3 -c '
import pathlib, tomllib
data = tomllib.loads(pathlib.Path("'"$release"'/source/pyproject.toml").read_text())
assert data["project"]["name"] == "kenpi"
assert data["build-system"]["requires"] == ["setuptools==78.1.1"]
assert data["project"]["dependencies"] == ["cryptography>=43"]
'

python3 -m venv "$release/venv"
"$release/venv/bin/python" -m pip install --no-cache-dir \
	--disable-pip-version-check 'setuptools==78.1.1'
"$release/venv/bin/python" -m pip install --no-cache-dir \
	--disable-pip-version-check --only-binary=:all: 'cryptography>=43'
"$release/venv/bin/python" -m pip install --no-cache-dir \
	--disable-pip-version-check --no-deps --no-build-isolation "$release/source"
"$release/venv/bin/python" -I -c \
	'import importlib.metadata; assert importlib.metadata.version("kenpi")'

python3 -I -c '
import tomllib
with open("/etc/kenpi/status.toml", "rb") as handle:
    data = tomllib.load(handle)
assert data == {"kenpi": {
    "environment": "production", "api_host": "127.0.0.1", "api_port": 8080,
    "camera_enabled": False, "vehicle_uart_enabled": False,
}}, "image configuration must retain the safe production defaults"
'

rm -rf -- "$release/source"
chown -hR root:root "$release"
chmod -R go-w "$release/venv"
ln -sfn "releases/$commit" /opt/kenpi/current

systemd-analyze verify /etc/systemd/system/kenpi-status.service
systemctl enable kenpi-status.service
