#!/usr/bin/env python3
"""Mint a bcrypt hash for ADMIN_PASSWORD_HASH.

    $ python scripts/hash_password.py
    Password: ********
    Confirm:  ********
    ADMIN_PASSWORD_HASH=$2b$12$...
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from security import hash_password


def main() -> int:
    p1 = getpass.getpass("Password: ")
    if not p1:
        print("Empty password rejected.", file=sys.stderr)
        return 2
    p2 = getpass.getpass("Confirm:  ")
    if p1 != p2:
        print("Passwords do not match.", file=sys.stderr)
        return 2
    print(f"ADMIN_PASSWORD_HASH={hash_password(p1)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
