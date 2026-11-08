"""Operator tool for the voice model catalog: list, pin checksums, install verified files.

Pinning records the SHA-256 of a file *you* downloaded from the official source (trust on
first use). Compare it with the publisher's checksum when one is available.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kenpi.features import DEFAULT_STATE_DIRECTORY
from kenpi.voice.catalog import ModelCatalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kenpi-models", description="Manage Kenpi voice models"
    )
    parser.add_argument("--state-directory", type=Path, default=DEFAULT_STATE_DIRECTORY)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show catalog entries and their status")
    pin = sub.add_parser("pin", help="record the SHA-256 of a downloaded file")
    pin.add_argument("model_id")
    pin.add_argument("file", type=Path)
    pin.add_argument(
        "--accept-license",
        action="store_true",
        help="confirm you have read the model's license terms",
    )
    install = sub.add_parser(
        "install", help="verify a file against its pin and install it"
    )
    install.add_argument("model_id")
    install.add_argument("file", type=Path)
    install.add_argument("--approve", action="store_true")
    args = parser.parse_args(argv)

    catalog = ModelCatalog(args.state_directory / "assistant" / "models")
    try:
        if args.command == "list":
            for item in catalog.describe():
                pinned = "pinned" if item["pinned"] else "unpinned"
                print(f"{item['id']:36} {item['status']:11} {pinned}")
        elif args.command == "pin":
            digest = catalog.pin(args.model_id, args.file, args.accept_license)
            print(f"Pinned {args.model_id}: {digest}")
        else:
            print(catalog.install_from_file(args.model_id, args.file, args.approve))
    except (ValueError, PermissionError, OSError) as error:
        print(f"Failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
