#!/usr/bin/env python3
"""Create private Compose configuration without displaying or replacing secrets."""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import secrets
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=Path(__file__).resolve().parents[1] / ".env.deploy",
        help="New configuration path; the parent directory must already exist.",
    )
    target = parser.parse_args().output
    content = (
        "# Private deployment configuration. Back up securely; never commit or share it.\n"
        "POSTGRES_PASSWORD=" + secrets.token_urlsafe(36) + "\n"
        "PA_WEB_ENCRYPTION_KEY=" + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii") + "\n"
        "PA_WEB_INVITE_CODE=" + secrets.token_urlsafe(24) + "\n"
        "PA_WEB_PORT=8000\n"
        "PA_WEB_IMAGE=pa-agent:local\n"
        "# Set to the actual reverse-proxy source address before enabling public HTTPS.\n"
        "PA_WEB_TRUSTED_PROXIES=127.0.0.1\n"
    )
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("Configuration already exists; refusing to overwrite it.", file=sys.stderr)
        return 1
    except OSError:
        print("Cannot create configuration. Check the destination directory and permissions.", file=sys.stderr)
        return 1
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
    except OSError:
        print("Configuration write failed. Inspect the destination locally before retrying.", file=sys.stderr)
        return 1
    print("Deployment configuration created with owner-only permissions; no secret values were displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
