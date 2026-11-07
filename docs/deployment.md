# Deployment readiness

Kenpi is not yet deployable as a system service. The repository currently
provides a Python package foundation and host-only tests, but no server entry
point, systemd unit, installer, upgrade procedure, or Raspberry Pi validation.

Run the host checks with the commands in the README. Do not enable a hardware
service based solely on the interfaces in this package. Deployment work must
include validated configuration, service supervision, health reporting,
sanitized diagnostics, clean shutdown, and a policy that never shuts down the
dashcam as part of Pi shutdown.
