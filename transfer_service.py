from datetime import date
from database import get_connection
from attendance_service import BRANCHES


def create_transfer(gm_id, employee_id, destination_branch,
                    start_date, end_date):

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        raise ValueError("Invalid transfer dates.")

    if end < start:
        raise ValueError("End date cannot be before start date.")

    if destination_branch not in BRANCHES:
        raise ValueError("Invalid destination branch.")

    with get_connection() as conn:
        gm = conn.execute("""
            SELECT role, is_active, must_change_pin
            FROM employees WHERE id = ?
        """, (gm_id,)).fetchone()

        if (not gm or gm["role"] != "GM"
                or not gm["is_active"]
                or gm["must_change_pin"]):
            raise ValueError("Active GM approval required.")

        employee = conn.execute("""
            SELECT id, full_name, branch, role, is_active
            FROM employees WHERE id = ?
        """, (employee_id,)).fetchone()

        if not employee or not employee["is_active"]:
            raise ValueError("Employee not found or inactive.")

        if employee["role"] in ("GM", "CEO"):
            raise ValueError("This account cannot be transferred.")

        if employee["branch"] == destination_branch:
            raise ValueError("Employee is already assigned to this branch.")

        overlapping = conn.execute("""
            SELECT id FROM transfers
            WHERE employee_id = ?
              AND status = 'APPROVED'
              AND start_date <= ?
              AND end_date >= ?
        """, (employee_id, end_date, start_date)).fetchone()

        if overlapping:
            raise ValueError(
                "Employee already has an approved transfer "
                "during these dates."
            )

        cursor = conn.execute("""
            INSERT INTO transfers (
                employee_id, destination_branch,
                start_date, end_date, status, approved_by
            )
            VALUES (?, ?, ?, ?, 'APPROVED', ?)
        """, (
            employee_id, destination_branch,
            start_date, end_date, gm_id
        ))

        return cursor.lastrowid


def list_transfers():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT t.id, e.full_name, e.branch AS home_branch,
                   t.destination_branch, t.start_date,
                   t.end_date, t.status
            FROM transfers t
            JOIN employees e ON e.id = t.employee_id
            ORDER BY t.start_date DESC, t.id DESC
        """).fetchall()

    return [dict(row) for row in rows]
