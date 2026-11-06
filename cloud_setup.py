import os
import sys

from auth import create_gm
from database import get_connection, initialize_database


def main():
    initialize_database()

    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM employees"
        ).fetchone()[0]

    if count:
        print("Cloud setup: database already has employees; skipping.")
        return

    name = os.environ.get("MRA_INITIAL_GM_NAME", "").strip()
    phone = os.environ.get("MRA_INITIAL_GM_PHONE", "").strip()
    pin = os.environ.get("MRA_INITIAL_GM_PIN", "")

    if not all((name, phone, pin)):
        print("Cloud setup: initial GM credentials are missing.")
        sys.exit(1)

    create_gm(name, phone, pin)
    print("Cloud setup: initial GM account created.")


if __name__ == "__main__":
    main()
