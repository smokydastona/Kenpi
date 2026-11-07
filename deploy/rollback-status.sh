#!/bin/bash
set -euo pipefail
if [[ $EUID -ne 0 || $# -ne 1 || ! $1 =~ ^[0-9a-f]{40}$ ]]; then
    echo "Usage: sudo bash deploy/rollback-status.sh <installed-full-commit-sha>" >&2
    exit 1
fi
base=/opt/kenpi
release=$base/releases/$1
if [[ -L $base || -L $base/releases || -L $release ||
      ! -x $release/venv/bin/python ]]; then
    echo "Release is not an installed Kenpi snapshot." >&2
    exit 1
fi
if [[ -L $base/.install.lock ]]; then
    echo "Refusing symlink install lock." >&2
    exit 1
fi
exec 9>"$base/.install.lock"
flock -n 9 || { echo "Another deployment is in progress." >&2; exit 1; }
if [[ $(stat -c %u "$release") != 0 ]] ||
    [[ -n $(find -P "$release" -xdev \
        \( ! -user root -o \( ! -type l -a -perm /6022 \) \) -print -quit) ]]; then
    echo "Refusing a release that is not root-owned and read-only to the service." >&2
    exit 1
fi
old_target=$(readlink "$base/current")
if [[ ! $old_target =~ ^releases/[0-9a-f]{40}$ ]]; then
    echo "Refusing unexpected current release target." >&2
    exit 1
fi
systemctl stop kenpi-status.service
restore() {
    status=$?
    if [[ $status -ne 0 ]]; then
        systemctl stop kenpi-status.service ||
            echo "Recovery could not stop the status service." >&2
        rm -f "$base/current.new"
        ln -s "$old_target" "$base/current.new"
        mv -Tf "$base/current.new" "$base/current"
        systemctl start kenpi-status.service ||
            echo "Recovery could not restart the previous status service." >&2
    fi
}
trap restore EXIT
ln -s "releases/$1" "$base/current.new"
mv -Tf "$base/current.new" "$base/current"
systemctl start kenpi-status.service
curl --fail --silent --show-error --retry 10 --retry-connrefused \
    --retry-delay 1 --connect-timeout 2 --max-time 5 --retry-max-time 60 \
    http://127.0.0.1:8080/healthz
systemctl is-active --quiet kenpi-status.service
echo
