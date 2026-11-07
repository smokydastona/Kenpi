"""Real Kenpi gateway on loopback for the Android :core interop tests.

Prints one JSON object per command to stdout. Commands (one per stdin line):
  pair           -> {"payload": "kenpi://pair?d=..."}
  approve <eid>  -> {"device_id": ...}
  deny <eid>     -> {"ok": true}
  revoke <id>    -> {"ok": true}
  event          -> {"ok": true}          (publishes a system event)
  quit
Run: python -m tests.android_interop_server
"""

import asyncio
import json
import sys
import tempfile

from kenpi.core.events import EventBus, EventType
from kenpi.gateway.server import GatewayServer
from kenpi.security.devices import DeviceRegistry
from kenpi.security.tls import ensure_certificate, server_context
from kenpi.tools.devices import pairing_payload


def out(obj):
    print(json.dumps(obj), flush=True)


async def main():
    tmp = tempfile.TemporaryDirectory()
    registry = DeviceRegistry(tmp.name)
    bus = EventBus(replay_size=64)
    cert, key, fingerprint = ensure_certificate(tmp.name, ["127.0.0.1"])
    gateway = GatewayServer(
        registry,
        bus,
        lambda scopes: {s: {"ok": True} for s in sorted(scopes)},
        ("127.0.0.1",),
        0,
        server_context(cert, key),
        heartbeat_seconds=0.5,
        idle_timeout_seconds=10.0,
        revalidate_seconds=0.2,
    )
    await gateway.start()
    port = gateway.bound_ports[0]
    out({"ready": True, "port": port, "fingerprint": fingerprint})
    loop = asyncio.get_running_loop()
    while True:
        line = (await loop.run_in_executor(None, sys.stdin.readline)).split()
        if not line or line[0] == "quit":
            break
        cmd = line[0]
        if cmd == "pair":
            eid, challenge, expires = registry.begin_pairing()
            out(
                {
                    "payload": pairing_payload(
                        "127.0.0.1", port, fingerprint, eid, challenge, expires
                    ),
                    "eid": eid,
                }
            )
        elif cmd == "approve":
            out({"device_id": registry.approve(line[1])})
        elif cmd == "deny":
            registry.deny(line[1])
            out({"ok": True})
        elif cmd == "revoke":
            registry.revoke(line[1])
            out({"ok": True})
        elif cmd == "event":
            bus.publish(EventType.SERVICE_STARTED, "interop", name="x")
            out({"ok": True})
    await gateway.stop()
    tmp.cleanup()


asyncio.run(main())
