# Firmware inventory

No ESP32 firmware project is currently implemented. The Python-side UART frame
definition and tests are in `kenpi/vehicle/protocol.py` and
`tests/test_vehicle_protocol.py`. Firmware implementation and cross-language
conformance tests remain required before connecting a physical controller.

Required firmware work includes versioned message handling, acknowledgements,
ignition/vehicle-state reports, heartbeat and Pi availability monitoring, wake
requests, bounded shutdown coordination, sensor reporting, OLED diagnostics,
watchdog recovery, and safe communication-loss behavior. No malformed or valid
message may be able to disable normal dashcam recording.

Camera (A810 Lite) firmware handling and the bench checklist are in
`docs/camera-firmware.md`.
