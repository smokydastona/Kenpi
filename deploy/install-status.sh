#!/bin/bash
# Install a committed snapshot; never run application builds as root.
set -euo pipefail
umask 022

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo; requires Python 3.11+, python3-venv, git and systemd." >&2
    exit 1
fi
if [[ $# -gt 1 ]]; then
    echo "Usage: $0 [full-commit-sha]" >&2
    exit 1
fi
source_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
commit=$(git -C "$source_root" rev-parse --verify "${1:-HEAD}^{commit}")
[[ $commit =~ ^[0-9a-f]{40}$ ]] || exit 1
if [[ $# -eq 1 && $1 != "$commit" ]]; then
    echo "Use a full commit SHA, not a moving branch or tag." >&2
    exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
for command in runuser useradd groupadd install tar systemctl systemd-analyze curl flock; do
    command -v "$command" >/dev/null
done

if ! getent group kenpi >/dev/null; then
    groupadd --system kenpi
fi
if ! getent passwd kenpi >/dev/null; then
    useradd --system --gid kenpi --home-dir /nonexistent \
        --shell /usr/sbin/nologin kenpi
fi
IFS=: read -r _ _ uid gid _ home shell < <(getent passwd kenpi)
if [[ $uid -eq 0 || $home != /nonexistent || $shell != /usr/sbin/nologin ]] ||
    [[ $(id -Gn kenpi) != kenpi ]] ||
    [[ $gid != "$(getent group kenpi | cut -d: -f3)" ]]; then
    echo "Refusing an existing kenpi account with unexpected privileges." >&2
    exit 1
fi

base=/opt/kenpi
release=$base/releases/$commit
unit=/etc/systemd/system/kenpi-status.service
config=/etc/kenpi/status.toml
for path in "$base" "$base/releases" /etc/kenpi "$unit" "$config"; do
    if [[ -L $path ]]; then
        echo "Refusing symlink at $path" >&2
        exit 1
    fi
done
install -d -o root -g root -m 0755 "$base" "$base/releases"
install -d -o root -g kenpi -m 0750 /etc/kenpi
if [[ -L $base/.install.lock ]]; then
    echo "Refusing symlink install lock." >&2
    exit 1
fi
exec 9>"$base/.install.lock"
flock -n 9 || { echo "Another deployment is in progress." >&2; exit 1; }
old_target=
if [[ -L $base/current ]]; then old_target=$(readlink "$base/current"); fi
if [[ -e $base/current && ! -L $base/current ]]; then
    echo "Refusing non-symlink current release." >&2
    exit 1
fi
if [[ -n $old_target && ! $old_target =~ ^releases/[0-9a-f]{40}$ ]]; then
    echo "Refusing unexpected current release target." >&2
    exit 1
fi
was_active=false
systemctl is-active --quiet kenpi-status.service && was_active=true
was_enabled=false
systemctl is-enabled --quiet kenpi-status.service && was_enabled=true
backup=$base/install-backup-$$
mkdir -m 0700 "$backup"
if [[ -e $unit ]]; then cp -p "$unit" "$backup/unit"; fi
if [[ -e $config ]]; then cp -p "$config" "$backup/config"; fi
systemctl stop kenpi-status.service || {
    rm -rf -- "$backup"
    [[ ! -e $unit ]] || exit 1
}
created=false
switched=false
cleanup() {
    status=$?
    if [[ $status -ne 0 ]]; then
        systemctl stop kenpi-status.service ||
            echo "Recovery could not stop the status service." >&2
        if $switched; then
            rm -f -- "$base/current"
            if [[ -n $old_target ]]; then
                ln -s "$old_target" "$base/current"
            fi
        fi
        if $created; then rm -rf -- "$release"; fi
        if ! $was_enabled; then
            systemctl disable kenpi-status.service ||
                echo "Recovery could not disable the status service." >&2
        fi
        if [[ -f $backup/unit ]]; then
            cp -p "$backup/unit" "$unit"
        else
            rm -f -- "$unit"
        fi
        if [[ -f $backup/config ]]; then
            cp -p "$backup/config" "$config"
        else
            rm -f -- "$config"
        fi
        systemctl daemon-reload || echo "Recovery could not reload systemd." >&2
        if $was_active; then
            systemctl start kenpi-status.service ||
                echo "Recovery could not restart the previous status service." >&2
        fi
    fi
    rm -rf -- "$backup"
}
trap cleanup EXIT
# Privileged installation inputs never come from the builder-owned release.
for relative in deploy/kenpi-status.service deploy/status.toml; do
    mode=$(git -C "$source_root" ls-tree "$commit" -- "$relative" | cut -d' ' -f1)
    if [[ $mode != 100644 ]]; then
        echo "Deployment metadata must be a regular committed file." >&2
        exit 1
    fi
    protected=$backup/${relative##*/}
    git -C "$source_root" show "$commit:$relative" > "$protected"
    chmod 0600 "$protected"
    if [[ -L $protected || ! -f $protected ||
          $(stat -c %u "$protected") != 0 ]]; then
        echo "Refusing unprotected deployment metadata." >&2
        exit 1
    fi
    expected=$(git -C "$source_root" rev-parse "$commit:$relative")
    if [[ $(git -C "$source_root" hash-object "$protected") != "$expected" ]]; then
        echo "Deployment metadata does not match the selected commit." >&2
        exit 1
    fi
done
python3 -I -c '
import configparser, pathlib, sys, tomllib
data = tomllib.loads(pathlib.Path(sys.argv[1]).read_text())
assert data == {"kenpi": {
    "environment": "production", "api_host": "127.0.0.1", "api_port": 8080,
    "camera_enabled": False, "vehicle_uart_enabled": False,
}}, "deployment configuration must retain the safe production defaults"
assert data["kenpi"]["camera_enabled"] is False
assert data["kenpi"]["vehicle_uart_enabled"] is False
unit = configparser.ConfigParser(interpolation=None, strict=False)
unit.optionxform = str
unit.read_string(pathlib.Path(sys.argv[2]).read_text())
service = unit["Service"]
assert service["User"] == "kenpi" and service["Group"] == "kenpi"
assert service["ExecStart"] == (
    "/opt/kenpi/current/venv/bin/python -m kenpi.main "
    "--config /etc/kenpi/status.toml"
)
assert {key for key in service if key.startswith("Exec")} == {"ExecStart"}
assert "SupplementaryGroups" not in service
assert service["NoNewPrivileges"] == "true"
assert service["CapabilityBoundingSet"] == service["AmbientCapabilities"] == ""
assert service["PrivateDevices"] == "true" and service["ProtectSystem"] == "strict"
' "$backup/status.toml" "$backup/kenpi-status.service"
if [[ -e $release || -L $release ]]; then
    echo "Release already exists; use rollback-status.sh to select it." >&2
    exit 1
fi
mkdir -m 0700 "$release"
created=true
git -C "$source_root" archive "$commit" > "$release/source.tar"
# Prepare leaf directories while the root-owned parent is inaccessible to builders.
install -d -o kenpi -g kenpi -m 0755 "$release/source" "$release/venv"
install -d -o kenpi -g kenpi -m 0700 "$release/build-tmp"
chmod 0755 "$release"
runuser -u kenpi -- tar -xf "$release/source.tar" -C "$release/source"
runuser -u kenpi -- python3 -c '
import pathlib, sys, tomllib
data = tomllib.loads(pathlib.Path(sys.argv[1]).read_text())
assert data["project"]["name"] == "kenpi"
assert data["project"]["version"] == "0.1.0"
assert data["build-system"]["requires"] == ["setuptools==78.1.1"]
assert not data["project"].get("dependencies")
' "$release/source/pyproject.toml"
runuser -u kenpi -- env TMPDIR="$release/build-tmp" \
    python3 -m venv "$release/venv"
runuser -u kenpi -- env TMPDIR="$release/build-tmp" \
    "$release/venv/bin/python" -m pip install --no-cache-dir \
    --disable-pip-version-check 'setuptools==78.1.1'
runuser -u kenpi -- env TMPDIR="$release/build-tmp" \
    "$release/venv/bin/python" -m pip install --no-cache-dir \
    --disable-pip-version-check --no-deps --no-build-isolation "$release/source"
runuser -u kenpi -- "$release/venv/bin/python" -I -c \
    'import importlib.metadata; assert importlib.metadata.version("kenpi") == "0.1.0"'
if [[ -e $config ]] && ! cmp -s "$config" "$backup/status.toml"; then
    echo "Refusing to overwrite an existing modified status configuration." >&2
    exit 1
fi
install -o root -g kenpi -m 0640 "$backup/status.toml" "$config"
install -o root -g root -m 0644 "$backup/kenpi-status.service" "$unit"
rm -rf -- "$release/source" "$release/source.tar" "$release/build-tmp"
runuser -u kenpi -- chmod -R go-w "$release/venv"
chown -hR -P root:root "$release"
if [[ -n $(find -P "$release" -xdev \
    \( ! -user root -o \( ! -type l -a -perm /6022 \) \) -print -quit) ]]; then
    echo "Refusing a release with unsafe ownership or permissions." >&2
    exit 1
fi
ln -s "releases/$commit" "$base/current.new"
mv -Tf "$base/current.new" "$base/current"
switched=true
systemd-analyze verify "$unit"
systemctl daemon-reload
systemctl enable --now kenpi-status.service
curl --fail --silent --show-error --retry 10 --retry-connrefused \
    --retry-delay 1 --connect-timeout 2 --max-time 5 --retry-max-time 60 \
    http://127.0.0.1:8080/healthz
systemctl is-active --quiet kenpi-status.service
echo
echo "Installed $commit. Previous release: ${old_target:-none}"
