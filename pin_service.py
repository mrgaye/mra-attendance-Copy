import hmac
from auth import hash_pin, verify_pin
from database import get_connection

def change_pin(employee_id, current_pin, new_pin, confirm_pin):
    if new_pin != confirm_pin:
        raise ValueError("New PINs do not match.")

    if not new_pin.isascii() or not new_pin.isdecimal() or len(new_pin) != 6:
        raise ValueError("New PIN must contain exactly 6 digits.")

    if hmac.compare_digest(current_pin, new_pin):
        raise ValueError("New PIN must be different from your current PIN.")

    with get_connection() as conn:
        employee = conn.execute("""
            SELECT pin_hash, is_active
            FROM employees
            WHERE id = ?
        """, (employee_id,)).fetchone()

        if not employee or not employee["is_active"]:
            raise ValueError("Account is unavailable.")

        if not verify_pin(current_pin, employee["pin_hash"]):
            raise ValueError("Current PIN is incorrect.")

        conn.execute("""
            UPDATE employees
            SET pin_hash = ?,
                must_change_pin = 0,
                auth_version = auth_version + 1
            WHERE id = ?
        """, (hash_pin(new_pin), employee_id))

    return True
