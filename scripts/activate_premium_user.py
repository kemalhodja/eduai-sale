#!/usr/bin/env python3
"""Grant premium to a user by email (requires INTERNAL_UPGRADE_SECRET on API).

Gereken ortam degiskenleri:
    API_URL                  (orn: https://talkcash-api-prod.onrender.com)
    USER_EMAIL               hedef kullanici
    PREMIUM_PLAN             (varsayilan: pro)
    INTERNAL_UPGRADE_SECRET  sunucudaki gizli anahtar (default YOK)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("API_URL", "")
EMAIL = os.environ.get("USER_EMAIL", "")
PLAN = os.environ.get("PREMIUM_PLAN", "pro")
SECRET = os.environ.get("INTERNAL_UPGRADE_SECRET", "")


def req(method: str, path: str, data: dict | None = None) -> tuple[int, dict]:
    headers = {
        "Content-Type": "application/json",
        "x-internal-upgrade-secret": SECRET,
    }
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return exc.code, payload


def main() -> int:
    global BASE
    missing = [name for name, value in (
        ("API_URL", BASE), ("USER_EMAIL", EMAIL), ("INTERNAL_UPGRADE_SECRET", SECRET),
    ) if not value]
    if missing:
        print("Eksik ortam degiskenleri: " + ", ".join(missing), file=sys.stderr)
        return 2
    BASE = BASE.rstrip("/") + "/api/v1"

    code, result = req("POST", "/billing/admin/upgrade", {"email": EMAIL, "plan": PLAN})
    if code != 200:
        print("admin upgrade failed:", code, result)
        return 1
    status = result.get("status", {})
    print(f"Premium activated for {EMAIL}")
    print(f"  plan={status.get('plan')} status={status.get('status')} is_premium={status.get('is_premium')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
