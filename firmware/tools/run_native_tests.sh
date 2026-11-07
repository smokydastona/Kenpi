#!/usr/bin/env bash
# Compiles and runs the host-executable firmware unit tests with the
# system C++ toolchain. This requires no PlatformIO installation, no
# Espressif toolchain, and no network access: it verifies the
# hardware-independent protocol and state-machine logic only.
#
# Usage: firmware/tools/run_native_tests.sh
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
firmware_dir="${root_dir}/firmware"
build_dir="${firmware_dir}/.native-build"

mkdir -p "${build_dir}"

compile_and_run() {
  local name="$1"
  shift
  local sources=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do
    sources+=("$1")
    shift
  done
  [[ $# -gt 0 ]] && shift # drop the "--" separator; the rest are run arguments
  echo "==> building ${name}"
  g++ -std=c++17 -Wall -Wextra -Werror -I"${firmware_dir}/include" \
    "${sources[@]}" -o "${build_dir}/${name}"
  echo "==> running ${name}"
  "${build_dir}/${name}" "$@"
}

compile_and_run test_protocol \
  "${firmware_dir}/src/protocol.cpp" \
  "${firmware_dir}/test/test_protocol.cpp"

compile_and_run test_power_state_machine \
  "${firmware_dir}/src/power_state_machine.cpp" \
  "${firmware_dir}/src/board_config.cpp" \
  "${firmware_dir}/test/test_power_state_machine.cpp"

compile_and_run test_shared_fixtures \
  "${firmware_dir}/src/protocol.cpp" \
  "${firmware_dir}/src/messages.cpp" \
  "${firmware_dir}/test/test_shared_fixtures.cpp" \
  -- "${root_dir}/protocol/vehicle_frames.txt"

echo "All native firmware tests passed."
