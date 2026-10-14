"""Create an API key. The raw key is printed once and never stored.

    python -m app.createkey <name>
"""
import sys

from app.auth import generate_key, hash_key
from app.db import SessionLocal
from app.models import ApiKey


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m app.createkey <name>")
        raise SystemExit(1)

    name = sys.argv[1]
    raw = generate_key()

    db = SessionLocal()
    try:
        db.add(ApiKey(key_hash=hash_key(raw), name=name))
        db.commit()
    finally:
        db.close()

    print(f"API key created for '{name}':\n")
    print(raw)
    print("\nStore it now. Only its hash is saved; it cannot be recovered.")


if __name__ == "__main__":
    main()
