# Deployment readiness

The local read-only status service can be started with
`kenpi --config config/kenpi.example.toml`. The repository does not yet include a
systemd unit, installer, upgrade procedure, or Raspberry Pi validation.

The server binds to loopback and exposes only health, capability, and static UI
routes. Do not enable a hardware service based solely on the interfaces in this
package. Deployment work must include service supervision, sanitized diagnostics,
clean shutdown, and a policy that never shuts down the dashcam as part of Pi
shutdown.
