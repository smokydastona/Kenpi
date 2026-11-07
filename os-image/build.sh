#!/bin/bash
# Build a minimal, Lite-based Raspberry Pi OS (64-bit) image for Kenpi.
#
# This fetches the official RPi-Distro/pi-gen tool (pinned by commit) and runs
# it with a single extra stage (stage-kenpi, next to it in this directory)
# appended after stage2, producing a Lite image with the kenpi status service
# preinstalled and enabled (not started) for first boot. No camera, GPIO,
# UART, or vehicle integration is enabled by this image: it ships exactly the
# same loopback-only read-only status service documented in deploy/.
#
# This has NOT been run to completion in the Kenpi development/CI environment:
# a full debootstrap+qemu ARM64 build takes a privileged container, loop
# devices, and a long time, none of which this project asserts are available
# everywhere this script runs. Treat a successful local build as your own
# verification, not as something this repository's CI has validated.
set -euo pipefail

PI_GEN_REPO="https://github.com/RPi-Distro/pi-gen.git"
# Pinned to a commit on the arm64 branch (Pi 5 is a 64-bit-only target); bump
# deliberately and re-review stage2's package list and defaults when you do.
PI_GEN_COMMIT="9bf6c9ae27c71a967a78694885e3ead61ca53c28"

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$here/.." && pwd)
work_dir="${KENPI_OS_WORK_DIR:-$here/.pi-gen}"
commit=$(git -C "$repo_root" rev-parse --verify "${1:-HEAD}^{commit}")

for command in git docker; do
	command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 1; }
done

if [[ ! -d $work_dir/.git ]]; then
	git clone --filter=blob:none "$PI_GEN_REPO" "$work_dir"
fi
git -C "$work_dir" fetch --depth 1 origin "$PI_GEN_COMMIT"
git -C "$work_dir" checkout --detach "$PI_GEN_COMMIT"
git -C "$work_dir" clean -xdf

# Export only our stage's image; the plain Lite image from stage2 is not
# also exported, to keep the deploy/ directory output unambiguous.
touch "$work_dir/stage2/SKIP_IMAGES"

cat > "$work_dir/config" <<EOF
IMG_NAME='kenpi'
PI_GEN_RELEASE='Kenpi (unofficial, based on Raspberry Pi OS)'
RELEASE='${KENPI_OS_RELEASE:-bookworm}'
STAGE_LIST='stage0 stage1 stage2 ${here}/stage-kenpi'
ENABLE_SSH='${KENPI_OS_ENABLE_SSH:-0}'
LOCALE_DEFAULT='${KENPI_OS_LOCALE:-en_US.UTF-8}'
TIMEZONE_DEFAULT='${KENPI_OS_TIMEZONE:-Etc/UTC}'
TARGET_HOSTNAME='${KENPI_OS_HOSTNAME:-kenpi}'
FIRST_USER_NAME='${KENPI_OS_USER:-pi}'
EOF
# FIRST_USER_PASS is deliberately left unset: with FIRST_USER_NAME=pi this
# makes the first boot run Raspberry Pi OS's own setup wizard to choose a
# password, rather than this pipeline baking one in.
if [[ -n ${KENPI_OS_FIRST_USER_PASS:-} ]]; then
	echo "FIRST_USER_PASS='${KENPI_OS_FIRST_USER_PASS}'" >> "$work_dir/config"
fi

export KENPI_REPO="$repo_root"
export KENPI_COMMIT="$commit"
export PIGEN_DOCKER_OPTS="-e KENPI_REPO=${KENPI_REPO} -e KENPI_COMMIT=${KENPI_COMMIT} -v ${KENPI_REPO}:${KENPI_REPO}:ro ${PIGEN_DOCKER_OPTS:-}"

cd "$work_dir"
exec ./build-docker.sh
