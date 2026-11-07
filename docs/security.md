# Security status

The sample configuration contains no credentials. The implemented HTTP API
binds to loopback in every environment and exposes only health, declared
capabilities, and static dashboard assets. It has no TLS, authentication,
camera commands, media routes, or WebSocket endpoint. Camera authentication is
externally blocked by missing A810 Lite protocol evidence.

The server bounds concurrent request handlers to 16 and client socket waits to
five seconds. Excess connections are closed rather than queued without a bound.
The dashboard bounds refresh requests, avoids overlapping polls, and clears
previous values after failures. These controls do not make the HTTP server
suitable for direct public exposure.

Command orchestration requires a classified operation, E4 or stronger evidence,
an operation-specific validator that explicitly approves its bytes payload, and
explicit user initiation for writes. Requests are serialized. Optional session
checks reject inactive, expired, and invalid snapshots without automatically
authenticating or retrying. Test-mode evidence bypass is confined to isolated
test construction and is not exposed by runtime configuration.

Remote clients require a separately secured HTTPS gateway on an access-controlled
private network or VPN; that gateway is not supplied by this package. Never
change the API bind address to make the Android companion connect. Future camera
and media integrations must validate all command and file inputs, use bounded network operations,
avoid logging credentials/session tokens, and keep camera credentials outside
version control. Treat packet captures as sensitive: redact secrets and remove
private video/GPS before preserving or sharing.

No credential storage format is invented before actual camera authentication is
known. Do not put future credentials in TOML files checked into the repository.
Keep privileged deployment configuration root-owned and logs free of raw
requests, responses, session values, and private media metadata.
