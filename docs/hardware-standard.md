# Locked hardware standard

The project target remains Raspberry Pi 5 (8 GB); Raspberry Pi 4B (4 GB or
8 GB) is a development/compatibility alternative. No hardware-selection change
is proposed.

The auxiliary-controller target remains the SX1262 LoRa V3 ESP32-S3 board in the
Heltec WiFi LoRa 32 V3 family. Exact board revision and pinout must be checked
against the physical board before wiring. Wired UART is the preferred local
link; LoRa is optional. CAN needs a separate suitable transceiver and automotive
protection. Never connect vehicle voltage or CAN directly to GPIO.

Hardware is not present/verified in this repository environment. No Pi
performance, thermal, electrical, or board-revision claims are made.
