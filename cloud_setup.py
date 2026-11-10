import os
import sys

from auth import create_gm
from database import get_connection, initialize_database


def main():
    initialize_database()

    with get_connection() as conn:
        branches = [
            ("SHOP-01 Senegambia", None, None, 100),
            ("SHOP-02 Jambangjelly", None, None, 100),
            ("SHOP-03 BB Kotu", 13.464111, -16.703667, 100),
        ]
        for branch in branches:
            conn.execute(
                """
                INSERT INTO branches
                    (name, latitude, longitude, gps_radius_metres)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (name) DO NOTHING
                """,
                branch,
            )
        conn.commit()
    print("Cloud setup: MRA branches initialized.")

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
