#!/bin/bash -e
# Host-side (non-chroot) step: copy a trusted, pinned source snapshot and the
# existing committed deployment metadata into the target rootfs. No build or
# install happens here; that is done inside the chroot by 02-run-chroot.sh so
# that the kenpi package is actually built for the target architecture.

if [[ -z ${KENPI_REPO:-} ]]; then
	echo "KENPI_REPO must point at a trusted Kenpi checkout (set by os-image/build.sh)" >&2
	exit 1
fi
if [[ -z ${KENPI_COMMIT:-} ]]; then
	echo "KENPI_COMMIT must be a full commit SHA (set by os-image/build.sh)" >&2
	exit 1
fi
if [[ ! $KENPI_COMMIT =~ ^[0-9a-f]{40}$ ]]; then
	echo "KENPI_COMMIT must be a full 40-character commit SHA" >&2
	exit 1
fi

release="${ROOTFS_DIR}/opt/kenpi/releases/${KENPI_COMMIT}"
install -d -m 0755 "${ROOTFS_DIR}/opt/kenpi" "${ROOTFS_DIR}/opt/kenpi/releases"
install -d -m 0755 "$release/source"

# Export exactly the pinned commit, not the working tree, so the image matches
# a reviewed, committed snapshot rather than uncommitted local changes.
git -c safe.directory="$KENPI_REPO" -C "$KENPI_REPO" archive "$KENPI_COMMIT" | tar -x -C "$release/source"

install -d -m 0750 "${ROOTFS_DIR}/etc/kenpi"
install -m 0640 "$release/source/deploy/status.toml" "${ROOTFS_DIR}/etc/kenpi/status.toml"
install -d -m 0755 "${ROOTFS_DIR}/etc/systemd/system"
install -m 0644 "$release/source/deploy/kenpi-status.service" \
	"${ROOTFS_DIR}/etc/systemd/system/kenpi-status.service"
echo "$KENPI_COMMIT" > "${ROOTFS_DIR}/opt/kenpi/KENPI_COMMIT"
