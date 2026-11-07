# Security status

The sample configuration contains no credentials. Production configuration
binds the future API to loopback by default and rejects non-loopback binding
until authentication is implemented. Network services and camera authentication
are not implemented.

Future deployment must add an authenticated API before exposing it beyond
loopback, validate all command and file inputs, use bounded network operations,
avoid logging credentials/session tokens, and keep camera credentials outside
version control. Treat packet captures as sensitive: redact secrets and remove
private video/GPS before preserving or sharing.
