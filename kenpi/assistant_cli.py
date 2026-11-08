"""Operator command line for the optional assistant (requires the feature to be enabled)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kenpi.assistant import AssistantService
from kenpi.core.capabilities import INITIAL_CAPABILITIES
from kenpi.voice.characters import CHARACTERS
from kenpi.voice.engines import EngineUnavailable
from kenpi.features import DEFAULT_STATE_DIRECTORY, FeatureStore
from kenpi.services.health import collect_host_health


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kenpi-assist", description="Talk to or diagnose Kenpi"
    )
    parser.add_argument("--state-directory", type=Path, default=DEFAULT_STATE_DIRECTORY)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask", help="send a text request")
    ask.add_argument("text")
    ask.add_argument(
        "--approve", action="store_true", help="approve actions that ask first"
    )
    sub.add_parser("diagnose", help="run system diagnostics")
    fix = sub.add_parser(
        "fix", help="run an approved remediation action, then verify it"
    )
    fix.add_argument("action")
    fix.add_argument("--approve", action="store_true")
    sub.add_parser("tools", help="list available tools")
    sub.add_parser("characters", help="list the voice characters and the active one")
    choose = sub.add_parser("character", help="switch the active character")
    choose.add_argument("character")
    enroll = sub.add_parser(
        "enroll", help="bind an authorized reference recording to a character"
    )
    enroll.add_argument("character")
    enroll.add_argument("reference", type=Path, help="WAV recording you may use")
    enroll.add_argument(
        "--engine",
        choices=("openvoice_v2", "chatterbox"),
        default="openvoice_v2",
        help="chatterbox stores the reference WAV; openvoice_v2 stores a tone-colour embedding",
    )
    enroll.add_argument("--speaker", required=True, help="whose voice this is")
    enroll.add_argument(
        "--i-am-authorized",
        required=True,
        metavar="STATEMENT",
        help="record why you are allowed to use this voice",
    )
    unenroll = sub.add_parser("unenroll", help="remove an enrolled reference voice")
    unenroll.add_argument("character")
    speak = sub.add_parser("speak", help="synthesize text in the active character")
    speak.add_argument("text")
    speak.add_argument("--output", type=Path, required=True)
    listen = sub.add_parser(
        "listen", help="listen on a microphone, answer by voice (runs until stopped)"
    )
    listen.add_argument(
        "--device", default="", help="ALSA capture device (default: first found)"
    )
    listen.add_argument("--no-wake-word", action="store_true")
    listen.add_argument("--list-devices", action="store_true")
    args = parser.parse_args(argv)

    if not FeatureStore(args.state_directory).is_enabled("assistant"):
        print(
            "The assistant is disabled. Enable it with: kenpi-features enable assistant",
            file=sys.stderr,
        )
        return 2
    # Standalone process: only host evidence is available, so vehicle and camera stay "not verified".
    assistant = AssistantService(
        args.state_directory / "assistant",
        None,
        collect_host_health,
        INITIAL_CAPABILITIES,
    )
    if args.command == "listen":
        return _listen(assistant, args)
    if args.command == "ask":
        response = assistant.orchestrator.handle(args.text, "cli")
        if args.approve:
            # The operator's flag approves exactly the operations just requested.
            for outcome in response.outcomes:
                if outcome.approval_id:
                    result = assistant.orchestrator.approve(outcome.approval_id)
                    print(
                        result.result["message"] if result.executed else result.reason
                    )
        print(json.dumps(response.describe()) if args.json else response.message)
    elif args.command == "diagnose":
        report = assistant.diagnostics.run()
        print(json.dumps(report.describe()) if args.json else report.render_text())
    elif args.command == "fix":
        result, report = assistant.diagnostics.remediate(args.action, args.approve)
        print(
            json.dumps(result.describe())
            if args.json
            else f"{result.action_id}: {result.detail}"
        )
        if report is not None and not args.json:
            print(report.render_text())
        return 0 if result.executed else 1
    elif args.command == "characters":
        active = assistant.character.id
        for c in CHARACTERS:
            print(f"{'*' if c.id == active else ' '} {c.id}: {c.name} ({c.accent})")
    elif args.command in ("character", "enroll", "unenroll", "speak"):
        return _voice_command(assistant, args)
    else:
        print(json.dumps(assistant.tools.describe(), indent=2))
    return 0


def _listen(assistant: AssistantService, args: argparse.Namespace) -> int:
    from kenpi.assistant import AssistantSettings
    from kenpi.voice.audio import (
        AlsaCapture,
        AudioUnavailable,
        WhisperCppTranscriber,
        discover_devices,
        play_wav,
        run_listener,
    )

    settings = AssistantSettings.load(
        args.state_directory / "assistant" / "assistant.toml"
    )
    try:
        devices = discover_devices()
        if args.list_devices:
            for device in devices:
                print(f"{device.id}: {device.description}")
            return 0
        device = (
            args.device or settings.audio_device or (devices[0].id if devices else "")
        )
        if not device:
            raise AudioUnavailable("no capture device found")
        if settings.transcriber == "moonshine":
            from kenpi.voice.moonshine import MoonshineTranscriber

            transcriber = MoonshineTranscriber(
                settings.moonshine_model_path, settings.moonshine_arch
            )
            if not transcriber.available():
                raise AudioUnavailable(
                    "moonshine-voice or moonshine_model_path is not configured"
                )
        else:
            transcriber = WhisperCppTranscriber(
                settings.whisper_binary, Path(settings.whisper_model)
            )
            if not settings.whisper_model or not transcriber.available():
                raise AudioUnavailable(
                    "whisper.cpp binary or whisper_model is not configured"
                )
    except AudioUnavailable as error:
        print(
            f"Voice input unavailable (text-only still works): {error}", file=sys.stderr
        )
        return 1

    def handle(text: str) -> None:
        response = assistant.orchestrator.handle(text, "voice")
        print(response.message, flush=True)
        reply = assistant.speak(response.message)
        if reply.audio is None:
            return
        try:
            play_wav(reply.audio, settings.audio_device)
        except (AudioUnavailable, ValueError) as error:
            print(f"(no audio output: {error})", file=sys.stderr)

    wake_words = () if args.no_wake_word else settings.wake_words
    try:
        run_listener(
            AlsaCapture(device).frames(), transcriber.transcribe, handle, wake_words
        )
    except AudioUnavailable as error:
        print(f"Capture stopped: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


def _voice_command(assistant: AssistantService, args: argparse.Namespace) -> int:
    try:
        if args.command == "character":
            character = assistant.set_character(args.character)
            print(f"Active character: {character.name}")
        elif args.command == "enroll":
            relative = assistant.enroll_character_voice(
                args.character,
                args.reference.read_bytes(),
                args.speaker,
                args.i_am_authorized,
                args.engine,
            )
            print(f"Enrolled {relative}")
        elif args.command == "unenroll":
            assistant.remove_character_voice(args.character)
            print("Enrolled voice removed")
        else:
            result = assistant.speak(args.text)
            if result.audio is None:
                print(f"No engine could speak: {result.reason}", file=sys.stderr)
                return 1
            args.output.write_bytes(result.audio)
            note = " (fallback engine)" if result.fell_back else ""
            print(f"Wrote {args.output} using {result.engine}{note}")
    except (ValueError, OSError, EngineUnavailable) as error:
        print(f"Failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
