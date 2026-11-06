from datetime import datetime, timezone
from database import get_connection

def pending_attendance(manager_id):
    with get_connection() as conn:
        manager = conn.execute("""
            SELECT role, branch, is_active, must_change_pin
            FROM employees
            WHERE id = ?
        """, (manager_id,)).fetchone()

        if not manager or not manager["is_active"]:
            raise ValueError("Manager account unavailable.")

        if manager["must_change_pin"]:
            raise ValueError("PIN change required.")

        if manager["role"] not in ("MANAGER", "GM"):
            raise ValueError("Manager access required.")

        if manager["role"] == "GM":
            rows = conn.execute("""
                SELECT a.id, e.full_name, a.branch,
                       a.clock_in, a.verification_status,
                      a.gps_latitude, a.gps_longitude,
                      a.gps_accuracy, a.gps_distance, a.gps_verification_level,
                        a.clock_in_photo, a.clock_out_photo
                FROM attendance a
                JOIN employees e ON e.id = a.employee_id
                WHERE a.verification_status = 'PENDING'
                ORDER BY a.clock_in DESC
            """).fetchall()
        else:
            rows = conn.execute("""
                SELECT a.id, e.full_name, a.branch,
                       a.clock_in, a.verification_status,
                      a.gps_latitude, a.gps_longitude,
                      a.gps_accuracy, a.gps_distance, a.gps_verification_level,
                        a.clock_in_photo, a.clock_out_photo
                FROM attendance a
                JOIN employees e ON e.id = a.employee_id
                WHERE a.verification_status = 'PENDING'
                AND a.branch = ?
                ORDER BY a.clock_in DESC
            """, (manager["branch"],)).fetchall()

    return [dict(row) for row in rows]


def verify_attendance(manager_id, attendance_id, decision, physical_confirmed=False):
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError("Invalid verification decision.")

    with get_connection() as conn:
        manager = conn.execute("""
            SELECT role, branch, is_active, must_change_pin
            FROM employees WHERE id = ?
        """, (manager_id,)).fetchone()

        if not manager or not manager["is_active"]:
            raise ValueError("Manager account unavailable.")

        if manager["must_change_pin"]:
            raise ValueError("PIN change required.")

        if manager["role"] not in ("MANAGER", "GM"):
            raise ValueError("Manager access required.")

        record = conn.execute("""
            SELECT id, employee_id, branch, verification_status,
                   gps_verification_level
            FROM attendance WHERE id = ?
        """, (attendance_id,)).fetchone()

        if not record:
            raise ValueError("Attendance record not found.")

        if record["verification_status"] != "PENDING":
            raise ValueError("Attendance already reviewed.")

        if record["employee_id"] == manager_id:
            raise ValueError("You cannot verify your own attendance.")

        if (manager["role"] == "MANAGER"
                and manager["branch"] != record["branch"]):
            raise ValueError("This record belongs to another branch.")

        if (
            decision == "APPROVED"
            and record["gps_verification_level"] != "STANDARD"
            and physical_confirmed is not True
        ):
            raise ValueError(
                "Physical presence confirmation is required "
                "before approving low-confidence attendance."
            )

        now = datetime.now(timezone.utc).isoformat()

        cursor = conn.execute("""
            UPDATE attendance
            SET verification_status = ?,
                verified_by = ?,
                verified_at = ?
            WHERE id = ?
              AND verification_status = 'PENDING'
        """, (decision, manager_id, now, attendance_id))

        if cursor.rowcount != 1:
            raise ValueError("Attendance was already reviewed.")

    return True
