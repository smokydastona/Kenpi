# Evidence and validation

## Evidence scale

| Level | Meaning |
|---|---|
| E0 | Unverified hypothesis |
| E1 | Evidence from another 70mai model |
| E2 | A810 Lite-specific documentation or observation |
| E3 | Communication captured from an actual A810 Lite |
| E4 | Operation independently reproduced |
| E5 | Operation and resulting camera state independently verified |

No A810 Lite or physical vehicle evidence was available during this implementation.
No other-model research is presented as proof of A810 Lite compatibility.

## Mandatory gates

| Gate | Status | Evidence |
|---|---|---|
| Camera control | NOT VERIFIED | No actual setting changed, read back, and restored |
| Live preview | NOT VERIFIED | No actual camera stream received or decoded |
| Kenwood touchscreen | NOT VERIFIED | No physical display/touch validation |

## Host-only checks

`python -m unittest discover -v` exercises configuration validation, conservative
camera-command handling, milestone reporting, service rollback, and UART frame
parsing. All device transports used in tests are explicitly test doubles. These
results do not count as hardware acceptance.

## Required physical evidence

For camera control, retain the original value, request and response evidence,
independently read back the changed value, restore it, and verify restoration.
For preview, record the observed codec, transport, resolution, FPS, latency,
decoded-frame evidence, and reconnect behavior. For the Kenwood gate, capture
the actual head-unit display and verify physical touch input controls the running
application. Store only sanitized, consented captures; never commit credentials,
private media, or location-bearing data.

## Reproduction checklist

Run host checks from a clean checkout:

```sh
python -m pip install -e '.[dev]'
ruff format --check kenpi tests
ruff check kenpi tests
python -m compileall -q kenpi tests
python -m unittest discover -v
python -m pip wheel --no-deps --wheel-dir /tmp/kenpi-wheels .
```

Run Android's real build/test tasks separately as documented in
[Android companion](android-companion.md); isolated JVM tests do not prove APK
compilation or device compatibility. Start the local service, open its dashboard,
confirm that health values are observed or unavailable, then stop/restart the
service and verify stale values are cleared and polling recovers. This is an
application integration check, not Gate 3.

For hardware evidence, retain a reviewed record containing:

- Hardware/firmware/app versions and the exact bench topology.
- Gate 1: discovery/authentication evidence, original setting, change, camera
  acknowledgement, independent read-back, restoration, and restored read-back.
- Gate 2: actual received/decoded frame, codec/resolution, measured delivered
  FPS and latency with measurement method, and interruption/recovery trace.
- Gate 3: physical head-unit display and touch action correlated with a real
  camera command/read-back and preview update.
- Vehicle integration: verified schematic/pinout/protection, ignition
  debounce, startup and delayed shutdown traces, link-loss/watchdog/brownout
  behavior, and proof that dashcam recording continues independently.

All three gates remain NOT VERIFIED. No hardware was available, no camera
adapter/stream protocol or Kenwood touch path was established, and no firmware
pin map was verified. These are implementation prerequisites, not merely tests
that can be replaced by simulation.

## Implementation verification record

Host test suites were executed in separately owned implementation scopes:

| Scope | Result | Evidence |
|---|---|---|
| API, configuration, CLI, coordinator, lifecycle | 37 passed | IPv6 requests/restart, handler limits, idle-client closure, degraded health, signal shutdown, startup rollback and cancellation |
| Command policy and UART | 35 passed | Evidence/validator gates, session expiry, timeouts, codec/transport failures, cancellation, serialization, malformed/partial frames and known CRC vector |
| Platform, health and deployment policy | 21 passed | Device-tree/thermal/uptime observations, sanitized unavailable diagnostics, safe configuration and protected deployment artifacts |
| Android client/store, isolated JVM | 20 passed | URL validation, stale/obsolete responses, polling cancellation and additive health labels |

The host scopes total 93 tests; this record does not claim a later monolithic
suite run. Ruff checks passed for each changed Python scope. Shell syntax and
ShellCheck passed for deployment scripts. A setuptools wheel was built and its
packaged Python modules and UI assets matched the final source. Android Activity
syntax was checked with the pinned Kotlin parser, but the actual APK build/test
tasks remain blocked by Google Maven DNS.

Browser integration verified real loopback health/capability responses, platform
and collection diagnostics, removal of previous data after disconnection, and
poll recovery after service restart. No camera or physical head unit was used.

A read-only implementation review found and prompted fixes for misplaced Android
lifecycle overrides and privileged installer trust-boundary flaws. Root-owned
deployment metadata and immutable release parents now separate privileged
installation from unprivileged builds. Physical installation, reboot behavior,
firmware, and all three hardware gates remain unverified.

## Implementation register

`validation/register.toml` lists every capability with implementation status, host test files and evidence level. `python -m kenpi.tools.register` (also run by the test suite) fails if a register entry disagrees with the runtime capabilities, references a missing test, omits what is missing for an incomplete capability, or claims E4+ without `evidence_ref`.

## Generated reports

`python -m kenpi.tools.report [--output validation] [--skip-tests]` validates the
register, then writes `implementation-report.json`, `hardware-readiness.json`
(every hardware capability is `not_validated` unless E4+ evidence with a
`evidence_ref` is registered) and, unless skipped, `test-report.json` from a
real unittest run (failures and skips are recorded as such). Each file carries
the commit and whether the tree was dirty. The files are not committed; they
are only meaningful for the commit that generated them. Integration and
dependency-license reports are not generated yet.

## Integration and dependency audits

`python -m kenpi.tools.audit [--output validation] [--strict]` writes
`integration-report.json` (Python modules no entry point can import, i.e. implemented
but disconnected) and `dependency-licences.json` (installed Python runtime dependency
licences from package metadata; Android and firmware dependencies are listed with
verdict `unknown` because they are not audited). Licence data is evidence for review,
not legal clearance; `--strict` exits 1 on copyleft Python dependencies. Generated JSON
is not committed.
