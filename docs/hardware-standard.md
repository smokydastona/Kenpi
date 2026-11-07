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

The application identifies supported Pi 4B/Pi 5 models from kernel device-tree
compatibility strings and exposes observed platform/thermal/uptime data through
health. Unsupported or absent platform evidence is explicit; CPU architecture
alone is not treated as Pi identification. This read-only functionality needs
no separate application implementation for the two supported controllers.

Heltec research references are the [V3 datasheet](https://resource.heltec.cn/download/WiFi_LoRa_32_V3/HTIT-WB32LA_V3(Rev1.1).pdf),
[pinout](https://resource.heltec.cn/download/WiFi_LoRa_32_V3/HTIT-WB32LA%20(F)_V3.png),
[schematic](https://resource.heltec.cn/download/WiFi_LoRa_32_V3/HTIT-WB32LA%20(F)_V3_Schematic_Diagram.pdf),
and [hardware update log](https://wiki.heltec.org/docs/devices/open-source-hardware/esp32-series/lora-32/wifi-lora-32-v3/hardware-update-log).
Retrieval failed with DNS errors in this environment, so no pin assignment is
claimed as verified. A document revision is not the physical board revision.
Vehicle ignition voltage/polarity, protection circuitry, power switching, and
Pi wake wiring also require matching electrical evidence. Firmware and GPIO
control remain blocked rather than assigning guessed pins.
