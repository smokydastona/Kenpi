# Captures

Only sanitized, non-sensitive captures belong here. Never commit credentials,
authentication tokens, private video, GPS/location data, or unredacted packets.
No hardware captures are currently available.

Redact text captures (HTTP logs, exported packet text, app logs) before sharing:

    python -m kenpi.tools.redact raw.txt captures/sanitized.txt

The tool masks auth headers, token/password/SSID/device-ID key-values, MAC
addresses, GPS coordinates and NMEA sentences. It is best effort: review the
output manually, and keep originals and binary pcaps private (they are refused).
