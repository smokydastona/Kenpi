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
