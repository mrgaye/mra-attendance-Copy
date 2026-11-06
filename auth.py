import bcrypt
import re
from database import get_connection, initialize_database

def normalize_phone(phone):
    phone = re.sub(r"[\s\-()]", "", phone)
    if phone.startswith("+220"):
        phone = phone[4:]
    elif phone.startswith("00220"):
        phone = phone[5:]

    if not re.fullmatch(r"\d{7}", phone):
        raise ValueError("Enter a valid 7-digit Gambian phone number.")

    return "+220" + phone

def hash_pin(pin):
    if not re.fullmatch(r"\d{6}", pin):
        raise ValueError("PIN must contain exactly 6 digits.")
    return bcrypt.hashpw(
        pin.encode("utf-8"),
        bcrypt.gensalt(rounds=12)
    ).decode("utf-8")

def verify_pin(pin, stored_hash):
    if not re.fullmatch(r"\d{6}", pin):
        return False
    return bcrypt.checkpw(
        pin.encode("utf-8"),
        stored_hash.encode("utf-8")
    )

def create_gm(name, phone, pin):
    initialize_database()
    phone = normalize_phone(phone)

    if not name.strip():
        raise ValueError("Full name is required.")

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT COUNT(*) FROM employees"
        ).fetchone()[0]

        if existing:
            raise ValueError(
                "Initial setup is only available on an empty database."
            )

        conn.execute("""
            INSERT INTO employees
            (full_name, phone_number, pin_hash, role,
             branch, must_change_pin)
            VALUES (?, ?, ?, 'GM', NULL, 0)
        """, (name.strip(), phone, hash_pin(pin)))

    return True

def authenticate(phone, pin):
    phone = normalize_phone(phone)

    with get_connection() as conn:
        employee = conn.execute("""
            SELECT * FROM employees
            WHERE phone_number = ? AND is_active = 1
        """, (phone,)).fetchone()

    if employee and verify_pin(pin, employee["pin_hash"]):
        return dict(employee)

    return None
