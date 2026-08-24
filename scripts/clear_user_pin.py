#!/usr/bin/env python3
"""Clear PIN for a user by email (requires INTERNAL_UPGRADE_SECRET on API).

Gereken ortam degiskenleri:
    API_URL                  (orn: https://talkcash-api-prod.onrender.com)
    USER_EMAIL               hedef kullanici
    INTERNAL_UPGRADE_SECRET  sunucudaki gizli anahtar (default YOK)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("API_URL", "").rstrip("/")
EMAIL = os.environ.get("USER_EMAIL", "")
SECRET = os.environ.get("INTERNAL_UPGRADE_SECRET", "")


def main() -> int:
    global BASE
    missing = [name for name, value in (
        ("API_URL", BASE), ("USER_EMAIL", EMAIL), ("INTERNAL_UPGRADE_SECRET", SECRET),
    ) if not value]
    if missing:
        print("Eksik ortam degiskenleri: " + ", ".join(missing), file=sys.stderr)
        return 2
    BASE = BASE.rstrip("/") + "/api/v1"

    headers = {
        "Content-Type": "application/json",
        "x-internal-upgrade-secret": SECRET,
    }
    body = json.dumps({"email": EMAIL}).encode()
    req = urllib.request.Request(
        BASE + "/auth/admin/clear-pin",
        data=body,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print(resp.read().decode())
            return 0
    except urllib.error.HTTPError as exc:
        print(exc.read().decode(), file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
