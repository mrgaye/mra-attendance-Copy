import secrets
import sqlite3

from auth import hash_pin, normalize_phone
from database import get_connection

BRANCHES = {
    "SHOP-01 Senegambia",
    "SHOP-02 Jambangjelly",
    "SHOP-03 BB Kotu",
}

ROLES = {"MANAGER", "STAFF"}


def register_employee(name, phone, role, branch):
    name = name.strip()
    phone = normalize_phone(phone)

    if not name or len(name) > 150:
        raise ValueError("Enter a valid employee name.")

    if role not in ROLES:
        raise ValueError("Invalid employee role.")

    if branch not in BRANCHES:
        raise ValueError("Select a valid MRA branch.")

    temporary_pin = f"{secrets.randbelow(1000000):06d}"

    try:
        with get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO employees
                (full_name, phone_number, pin_hash,
                 role, branch, must_change_pin)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (
                name,
                phone,
                hash_pin(temporary_pin),
                role,
                branch
            ))
            employee_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(
            "This phone number is already registered."
        )

    return employee_id, temporary_pin


def list_employees(search="", branch="", role="", status=""):
    search = search.strip().casefold()
    employees = _list_all_employees()

    return [
        employee for employee in employees
        if (
            not search
            or search in employee["full_name"].casefold()
            or search in employee["phone_number"].casefold()
        )
        and (not branch or employee["branch"] == branch)
        and (not role or employee["role"] == role)
        and (
            not status
            or (status == "active" and employee["is_active"])
            or (status == "inactive" and not employee["is_active"])
        )
    ]


def _list_all_employees():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, full_name, phone_number,
                   role, branch, is_active
            FROM employees
            ORDER BY id DESC
        """).fetchall()

    return [dict(row) for row in rows]



def update_employee(employee_id, name, phone, role, branch):
    """Update an existing STAFF or MANAGER account."""

    name = name.strip()
    phone = normalize_phone(phone)

    if not name or len(name) > 150:
        raise ValueError("Enter a valid employee name.")

    if role not in ROLES:
        raise ValueError("Invalid employee role.")

    if branch not in BRANCHES:
        raise ValueError("Select a valid MRA branch.")

    try:
        with get_connection() as conn:
            employee = conn.execute("""
                SELECT id, role, is_active
                FROM employees
                WHERE id = ?
            """, (employee_id,)).fetchone()

            if employee is None:
                raise ValueError("Employee not found.")

            if employee["role"] not in ROLES:
                raise ValueError(
                    "GM and CEO accounts cannot be edited here."
                )

            conn.execute("""
                UPDATE employees
                SET full_name = ?,
                    phone_number = ?,
                    role = ?,
                    branch = ?
                WHERE id = ?
            """, (
                name,
                phone,
                role,
                branch,
                employee_id
            ))

    except sqlite3.IntegrityError:
        raise ValueError(
            "This phone number is already registered."
        )

    return True



def deactivate_employee(employee_id):
    """Deactivate a STAFF or MANAGER account."""

    with get_connection() as conn:
        employee = conn.execute("""
            SELECT id, full_name, role, is_active
            FROM employees
            WHERE id = ?
        """, (employee_id,)).fetchone()

        if employee is None:
            raise ValueError("Employee not found.")

        if employee["role"] not in ROLES:
            raise ValueError(
                "GM and CEO accounts cannot be deactivated here."
            )

        if not employee["is_active"]:
            raise ValueError(
                "This employee is already inactive."
            )

        conn.execute("""
            UPDATE employees
            SET is_active = 0
            WHERE id = ?
        """, (employee_id,))

    return True



def reactivate_employee(employee_id):
    """Reactivate an inactive STAFF or MANAGER account."""

    with get_connection() as conn:
        employee = conn.execute("""
            SELECT id, full_name, role, is_active
            FROM employees
            WHERE id = ?
        """, (employee_id,)).fetchone()

        if employee is None:
            raise ValueError("Employee not found.")

        if employee["role"] not in ROLES:
            raise ValueError(
                "GM and CEO accounts cannot be reactivated here."
            )

        if employee["is_active"]:
            raise ValueError(
                "This employee is already active."
            )

        cursor = conn.execute("""
            UPDATE employees
            SET is_active = 1
            WHERE id = ?
              AND is_active = 0
              AND role IN ('STAFF', 'MANAGER')
        """, (employee_id,))

        if cursor.rowcount != 1:
            raise ValueError(
                "Employee reactivation could not be completed."
            )

    return True



def reset_employee_pin(employee_id):
    """Generate a temporary PIN for an active STAFF or MANAGER."""

    with get_connection() as conn:
        employee = conn.execute("""
            SELECT id, full_name, role, is_active
            FROM employees
            WHERE id = ?
        """, (employee_id,)).fetchone()

        if employee is None:
            raise ValueError("Employee not found.")

        if employee["role"] not in ROLES:
            raise ValueError(
                "GM and CEO PINs cannot be reset here."
            )

        if not employee["is_active"]:
            raise ValueError(
                "Cannot reset the PIN of an inactive employee."
            )

        temporary_pin = f"{secrets.randbelow(1000000):06d}"
        temporary_hash = hash_pin(temporary_pin)

        cursor = conn.execute("""
            UPDATE employees
            SET pin_hash = ?,
                must_change_pin = 1,
                auth_version = auth_version + 1
            WHERE id = ?
              AND is_active = 1
              AND role IN ('STAFF', 'MANAGER')
        """, (temporary_hash, employee_id))

        if cursor.rowcount != 1:
            raise ValueError(
                "Employee PIN reset could not be completed."
            )

    return temporary_pin
