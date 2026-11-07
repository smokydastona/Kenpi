# Optional assistant: voice, AI and Diagnostics & AI Support

An optional subsystem, **off by default**, that adds a planner-based assistant, a diagnostics
engine with a known-issue database, and replaceable speech engines. It is designed from the
architecture of Mordecai-AI-Android (tool registry, planner/policy/executor separation, event
bus, preference memory), the OpenVoice engine contract, and the model-catalog idea from
awesome-ai-voice. No code from those projects is bundled, and nothing is downloaded at runtime.

## Turning it on and off

```sh
kenpi-features status
sudo kenpi-features enable assistant --apply    # --apply restarts kenpi-status.service
sudo kenpi-features disable assistant --apply
```

The flag lives in `<state_directory>/features.json` (default `/var/lib/kenpi`) and is written
only by the privileged CLI; the unprivileged service only reads it, so it cannot enable itself.
Missing or malformed state means disabled. When disabled nothing is imported, started or
exposed, and the assistant routes return `404 {"error":"feature_disabled"}`. Safe mode never
starts the assistant. Assistant data lives in `<state_directory>/assistant` (mode 0700).

## What it does and does not claim

- Camera recording state, preview and media access are reported **not verified** until
  capability evidence reaches E4 (`kenpi/core/capabilities.py`). Nothing is inferred from
  gateway connectivity.
- The assistant is read-only toward the camera. `stop_recording` is registered as `NEVER`
  and is refused even with approval.
- Microphone capture, wake word and speech recognition are **not implemented**: the
  `Transcriber` interface exists but no backend is bundled, and the microphone path is reported
  `not_validated`. Voice output requires an installed engine; with none, `speak` returns a reason.
- Failures are contained. The assistant is started outside the service coordinator and its
  start/stop never raise; the camera records independently of Kenpi.

## Architecture

| Package | Role |
| --- | --- |
| `kenpi/features.py` | Toggle store and `kenpi-features` CLI |
| `kenpi/assistant.py` | `AssistantService`: wiring, built-in tools, read-only API routes |
| `kenpi/intelligence/` | Tool registry + policy, rule-based planner (optional validated model provider), sessions, whitelisted preference memory, orchestrator |
| `kenpi/diagnostics/` | Check registry, log analyzer, fault rules, root-cause ranking, issue database, remediation, history, reports |
| `kenpi/voice/` | MeloTTS and OpenVoice engines, fallback service, voice profiles with consent, model catalog |
| `kenpi/core/permissions.py` | Shared permission levels |

Flow: text → planner (plan only) → policy (permission, approval, evidence gate) → tool →
result. A model provider's output is untrusted: unknown tools and invalid arguments are dropped.

### Permissions

| Level | Behaviour |
| --- | --- |
| `AUTOMATIC` | Read-only; runs without asking |
| `ASK` | Needs approval (e.g. restart a service, clear cache) |
| `EXPLICIT` | Needs explicit approval (config, install, firmware, reboot) |
| `STRICT` | Camera recording behaviour; needs approval **and** an evidence gate that passes |
| `NEVER` | Never runnable |

Operation ids cannot be registered below the minimum for their keyword (for example
`reboot_*`, `install_*`, `stop_record*`).

## Diagnostics

`kenpi-assist diagnose` (or `GET /api/v1/diagnostics/report`) runs non-invasive checks for the
host, network, vehicle link, capabilities, services and voice; analyzes in-process logs; applies
fault rules; and ranks causes by evidence and corroboration. Missing evidence is `unknown`,
never `ok`. `kenpi-assist fix <action> --approve` runs a registered remediation and re-runs the
checks to record whether the fault cleared. History is bounded and stored best-effort.

Not implemented: ESP32 firmware checks beyond the controller link, Kenwood touch/audio checks,
journal ingestion (only in-process logs), a dashboard or Android UI.

`kenpi-assist` runs as a standalone process with host evidence only; live vehicle and
coordinator evidence is available through the API of the running service.

## Voice

- **MeloTTS** (default, offline fallback) and **OpenVoice V2** (cloning) are both served by the local
  service below. Piper (GPL), Kokoro and sherpa-onnx (Apache-2.0) were removed to keep the
  candidate set MIT-only. **Chatterbox** (MIT) is catalogued as the preferred expressive
  engine, but no Chatterbox service or engine client exists yet; benchmark Turbo on a Pi 5 first
  or use an external GPU host.
- **OpenVoice V2**: an isolated inference service you run separately (own PyTorch environment,
  or an external GPU host) implementing `GET /health` and `POST /synthesize` (audio/wav). Set
  `openvoice_url` (and `openvoice_allow_remote = true` for non-loopback hosts) in
  `<assistant>/assistant.toml`. This service contract is Kenpi's own and `/extract` is used for enrollment. A reference
  implementation lives in `kenpi/voice/pipeline_server.py` (stdlib HTTP, loopback only, embeddings
  confined to the voice directory) with the PyTorch backend in `kenpi/voice/openvoice_backend.py`.
  **The backend is unvalidated**: it was written against the public OpenVoice V2 / MeloTTS APIs
  without PyTorch or checkpoints available, and is unbenchmarked on a Pi 5.
- **Profiles**: validated; any custom voice needs a recorded consent statement; embedding paths
  stay private; the default profile cannot be deleted. If the selected engine fails, the
  fallback engine is used.
- **Catalog**: lists candidate models with license and hardware metadata. Installs are copy-only
  from an operator-supplied file, require approval, and require a pinned SHA-256 that matches.
  Entries ship unpinned and with `license_verified = false`.

## API (read-only, GET)

`/api/v1/features` is always available. When the assistant is enabled:
`/api/v1/voice/{status,engines,catalog,profiles,characters}`, `/api/v1/intelligence/tools`,
`/api/v1/intelligence/plan?text=...` (plans only, never executes),
`/api/v1/diagnostics/{report,history}`. There are no command routes; execution stays in the
privileged CLI or a future authenticated gateway.

## OpenVoice V2 + MeloTTS service

In a separate virtualenv with `torch`, `melo-tts` and `openvoice` installed:

```
python -m kenpi.voice.pipeline_server --models /var/lib/kenpi/assistant/models \
    --voice-root /var/lib/kenpi/assistant/voice --port 8780
```

Then set `openvoice_url = "http://127.0.0.1:8780"` in `assistant.toml`. MeloTTS renders the
text in a base speaker (language and accent); an enrolled embedding recolours it with
OpenVoice's tone-colour converter. Without an enrolled embedding the Melo output is returned.

## Pinned checksums

No checksum ships in the catalog, on purpose: none could be verified offline, and an invented
hash would be worse than none. Pin what you actually downloaded from the official sources
(trust on first use; compare with the publisher's checksum where one exists):

```
kenpi-models list
kenpi-models pin openvoice-v2-converter ./checkpoint.pth --accept-license
kenpi-models install openvoice-v2-converter ./checkpoint.pth --approve
```

Pins live in `<assistant>/models/pins.json`. Installed files are re-hashed on every status read,
so a modified file shows as `failed`. Entries cover the OpenVoice V2 converter and base speaker
embeddings, MeloTTS English and French, and Chatterbox Turbo and Multilingual.

## Voice characters

| id | Character | Accent target | Base speaker |
|---|---|---|---|
| `brooklyn_gentleman` (default) | The Brooklyn Gentleman | Brooklyn / NY | `EN-US` |
| `english_professor` | The English Professor | British RP | `EN-BR` |
| `southern_fixer` | The Southern Fixer | Texas-Louisiana | `EN-US` |
| `madame_colette` | Madame Colette | Parisian-accented English | `EN-US` |

Each has its own personality wording, speed, MeloTTS fallback and voice profile
(`char_<id>`). The wording is added in front of the factual message and never replaces it, so
diagnostics, refusals and notifications stay accurate in every voice. The active character is a
preference: switch by voice ("switch to the professor"), or `kenpi-assist character <id>`.
Sessions, pending questions and diagnostic context are independent of the character.

**Accents are not guaranteed by a profile name.** MeloTTS has no Brooklyn, Southern or
French-accented-English speaker; `EN-BR` is the only close match. For the real accent, enroll a
recording you are authorized to use (`kenpi-assist enroll <id> clip.wav --speaker "Name"
--i-am-authorized "why"`); `unenroll` reverts. Do not enroll recordings of real people without
their consent. Without enrollment, only the wording differs.

The dashboard has a read-only **Assistant** screen (hidden while the feature is off). The
loopback API has no command routes, so selecting a character in the UI is deliberately not offered.

Licensing: MIT engines and checkpoints do not make a reference recording MIT. Enrolled recordings need their own documented rights; `enroll` records the speaker and your authorization statement only.


## Chatterbox service and engine client

Chatterbox is the expressive, reference-audio engine (id `chatterbox`). Run it in its own
virtualenv with `torch`, `torchaudio` and `chatterbox-tts`:

```
python -m kenpi.voice.pipeline_server --backend chatterbox --variant turbo --port 8781 \
    --models /var/lib/kenpi/assistant/models --voice-root /var/lib/kenpi/assistant/voice
```

Set `chatterbox_url = "http://127.0.0.1:8781"` in `assistant.toml` (a remote GPU host also needs
`openvoice_allow_remote = true`). Enroll a character with an authorized 3-60 s WAV:
`kenpi-assist enroll <character> clip.wav --speaker NAME --attest "..." --engine chatterbox`.
Chatterbox keeps the reference WAV itself (no embedding); the profile falls back to MeloTTS.

Honest status: the backend and client are **unvalidated against the real model**. Chatterbox
loads weights through the Hugging Face cache, so the catalog pin is for operator verification
only, not enforcement. Full models are not assumed real-time on a Pi 5: use Turbo or a GPU host.

## Companion phone: awareness and log exchange

Pair a phone with the `assistant.use` scope (not granted by default):

```
kenpi-devices pair --scopes status.read,vehicle.read,assistant.use
```

Authenticated gateway routes (bearer token + scope; `404 feature_disabled` when the assistant is
off; `403 scope_required` without the scope):

| Route | Purpose |
|---|---|
| `GET /api/v1/assistant/status` | Active character, phones connected, verified setup steps. |
| `GET /api/v1/assistant/diagnostics` | Full diagnostics report. |
| `GET /api/v1/assistant/logs` | Last Kenpi log lines, redacted. |
| `POST /api/v1/assistant/logs` | Phone uploads app version, connection state and <=200 lines. |
| `POST /api/v1/assistant/ask` | Ask the assistant `{"text": ...}`; never approves actions. |

Presence is taken from live gateway WebSocket sessions, so Kenpi knows which phones are connected
now. Phone logs are untrusted: bounded, redacted (tokens, pairing URIs, long secrets), in-memory
only, and used solely as diagnostic evidence (new `companion` subsystem and the
`companion.disconnected` fault). Without a running gateway presence is `unknown`. The "setup"
guide (`setup_help` tool, "help me set up") reports only steps it can verify. The app also has an
offline `LocalHelper` for connection problems when Kenpi cannot be reached.

Limits: the phone app has text chat only (no phone-side speech); Kotlin changes were written
without an Android toolchain and are **uncompiled and untested**.

## Model provider (optional)

Deterministic rules always run first. For requests no rule matches, an optional model may *propose*
tool calls. Configure any OpenAI-compatible endpoint (llama.cpp server, Ollama, vLLM) in `assistant.toml`:

```
llm_url = "http://127.0.0.1:8080"
llm_model = "your-model"
llm_timeout_seconds = 8
```

A non-loopback host needs `llm_allow_remote = true` and `https`. The model sees only tool names,
descriptions and argument names, and the user text (capped at 500 chars, marked untrusted). Its
output is JSON-parsed, unknown tools and invalid arguments are dropped, and every surviving step still
passes the permission policy and single-use approvals; the model cannot approve anything. Any
timeout, error or malformed reply falls back to the rule-based answer.

Tested against a real local HTTP server speaking the chat-completions protocol, not against an actual
language model: model quality, latency on a Pi 5 and multi-turn history are **not** evaluated or implemented.

## Log ingestion

`kenpi/diagnostics/ingest.py` keeps up to 5000 timestamped, secret-redacted records labelled by source
(`app`, `journal`, `gateway`, `android`, `firmware`, `voice`, `network`). Today the following are actually
wired: in-process app logs, the systemd journal for `journal_units` (default `kenpi-status.service`, read with
`journalctl -o json`; if it is missing or fails the journal is simply not ingested and that is logged), and
phone logs uploaded by the companion. `gateway`, `firmware`, `voice` and `network` are accepted source labels
but have no automatic collector yet. Phone (`android`) records are searchable but flagged untrusted and are
excluded from the fault analyzer, so a phone cannot create a Kenpi fault by sending crafted lines. Ask
"search the logs for <text>" (read-only `search_logs` tool).

## Tool execution safeguards

Each tool runs in a worker thread with a per-tool timeout (default 10 s); a hung tool yields a
failed outcome instead of blocking the assistant. An optional `verify` callback checks the result,
and a failed check is reported as a failure, not success. `Orchestrator.history` keeps the last 200
executions (tool, argument names only, outcome, duration) in memory. A timed-out thread cannot be
force-killed in Python; it is a daemon thread and its result is discarded.

## Host and network evidence

Checks read `/proc`, `/sys`, `/etc/resolv.conf`, the firmware throttle flags and the gateway
certificate: CPU load, swap, throttling/under-voltage, USB devices, default route, configured
resolvers, WireGuard interfaces and certificate days remaining. Unreadable sources report
`unknown`. Not yet covered: live DNS resolution, DHCP lease state, WireGuard peer handshakes,
filesystem errors and storage health.

## Audio input (`kenpi/voice/audio.py`)

Provided: mono 16-bit WAV validation, an energy-based voice activity detector, ALSA capture
device discovery and capture through `arecord`, a `whisper.cpp` CLI transcriber, a transcript-based
wake-word gate and `run_listener`, which ties them together. Everything raises `AudioUnavailable`
when its tool or device is missing; text-only operation is unaffected.

Not done: no systemd listener service is wired up yet, playback (`aplay`) is not implemented, the
wake word is matched on whisper transcripts (not a dedicated low-power detector), and nothing was
tested against a real microphone or real whisper.cpp model.

## Remediation workflow

A `RemediationAction` may declare a `precondition` (blocks execution with a reason), a `timeout`,
a `verify` check and a `rollback`. Execution order: permission/approval gate → precondition →
handler (worker thread, timeout) → verification → rollback on failure. Results record `verified`
and `rolled_back`, and the engine writes each to the diagnostic history. Existing actions
declare none of these yet, so they run unverified; add them per action when the real operation is
defined.

## Voice listening (`kenpi-assist listen`)

`kenpi-assist listen` captures from an ALSA device (`arecord`), segments speech
with an energy VAD, transcribes with whisper.cpp, requires a wake word
(`wake_words`, default `hey kenpi`; `--no-wake-word` disables), sends the text
through the normal orchestrator and plays the spoken reply with `aplay`.
Settings in `assistant.toml`: `whisper_binary`, `whisper_model`, `wake_words`,
`audio_device`. Without arecord/whisper/model it exits with a clear message;
text-only use is unaffected. Tested with real subprocess stand-ins only; it has
not been run against a real microphone, whisper model or speaker.
