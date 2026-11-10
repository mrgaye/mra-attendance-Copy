from datetime import datetime, timezone
import math

from database import get_connection
from attendance_service import authorized_branch, distance_metres


def request_gps_exception(
    employee_id, branch, latitude, longitude, accuracy, reason
):
    now = datetime.now(timezone.utc)
    authorized_branch(employee_id, branch, now.date().isoformat())

    if not all(math.isfinite(v) for v in (latitude, longitude, accuracy)):
        raise ValueError("Invalid GPS information.")

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("Invalid GPS coordinates.")

    if accuracy < 0:
        raise ValueError("Invalid GPS accuracy.")

    reason = reason.strip()
    if not reason or len(reason) > 500:
        raise ValueError("Provide a reason of up to 500 characters.")

    with get_connection() as conn:
        branch_settings = conn.execute("""
            SELECT latitude, longitude, is_active
            FROM branches WHERE name = ?
        """, (branch,)).fetchone()

        if not branch_settings or not branch_settings["is_active"]:
            raise ValueError("Branch unavailable.")

        if branch_settings["latitude"] is None or branch_settings["longitude"] is None:
            raise ValueError("Branch GPS coordinates not configured.")

        distance = distance_metres(
            latitude, longitude,
            branch_settings["latitude"], branch_settings["longitude"]
        )

        existing = conn.execute("""
            SELECT id FROM gps_exception_requests
            WHERE employee_id = ? AND status = 'PENDING'
            LIMIT 1
        """, (employee_id,)).fetchone()

        if existing:
            raise ValueError("You already have a pending GPS exception request.")

        open_shift = conn.execute("""
            SELECT id FROM attendance
            WHERE employee_id = ? AND clock_out IS NULL
            LIMIT 1
        """, (employee_id,)).fetchone()

        if open_shift:
            raise ValueError("You already have an active shift.")

        cursor = conn.execute("""
            INSERT INTO gps_exception_requests (
                employee_id, branch, requested_at,
                gps_latitude, gps_longitude, gps_accuracy,
                gps_distance, reason, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
        """, (
            employee_id, branch, now.isoformat(),
            latitude, longitude, accuracy,
            round(distance, 1), reason
        ))

        return cursor.lastrowid


def pending_gps_exceptions(manager_id):
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

        if manager["role"] not in ("GM", "MANAGER"):
            raise ValueError("Manager access required.")

        query = """
            SELECT
                r.id,
                r.employee_id,
                e.full_name,
                r.branch,
                r.request_type,
                r.requested_at,
                r.gps_latitude,
                r.gps_longitude,
                r.gps_accuracy,
                r.gps_distance,
                r.reason,
                r.status
            FROM gps_exception_requests r
            JOIN employees e ON e.id = r.employee_id
            WHERE r.status = 'PENDING'
        """

        params = []

        if manager["role"] == "MANAGER":
            query += " AND r.branch = ?"
            params.append(manager["branch"])

        query += " ORDER BY r.requested_at ASC"

        return conn.execute(query, params).fetchall()


def review_gps_exception(
    manager_id, request_id, decision,
    physical_confirmed=False, notes=""
):
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError("Invalid review decision.")

    if decision == "APPROVED" and physical_confirmed is not True:
        raise ValueError("Physical presence confirmation is required.")

    notes = (notes or "").strip()
    if len(notes) > 500:
        raise ValueError("Review notes must not exceed 500 characters.")

    now = datetime.now(timezone.utc).isoformat()

    with get_connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (int(request_id),))

        manager = conn.execute("""
            SELECT role, branch, is_active, must_change_pin
            FROM employees WHERE id = ?
        """, (manager_id,)).fetchone()

        if not manager or not manager["is_active"]:
            raise ValueError("Manager account unavailable.")

        if manager["must_change_pin"]:
            raise ValueError("PIN change required.")

        if manager["role"] not in ("GM", "MANAGER"):
            raise ValueError("Manager access required.")

        record = conn.execute("""
            SELECT * FROM gps_exception_requests
            WHERE id = ?
        """, (request_id,)).fetchone()

        if not record:
            raise ValueError("GPS Exception Request not found.")

        if record["status"] != "PENDING":
            raise ValueError("Request has already been reviewed.")

        if record["employee_id"] == manager_id:
            raise ValueError("You cannot review your own request.")

        if (
            manager["role"] == "MANAGER"
            and manager["branch"] != record["branch"]
        ):
            raise ValueError("Request belongs to another branch.")

        attendance_id = None
        confirmed_arrival = None

        if decision == "APPROVED":
            employee = conn.execute("""
                SELECT is_active, must_change_pin
                FROM employees WHERE id = ?
            """, (record["employee_id"],)).fetchone()

            if not employee or not employee["is_active"]:
                raise ValueError("Employee account unavailable.")

            if employee["must_change_pin"]:
                raise ValueError("Employee PIN change required.")

            authorized_branch(
                record["employee_id"],
                record["branch"],
                now[:10]
            )

            open_shift = conn.execute("""
                SELECT id FROM attendance
                WHERE employee_id = ?
                  AND clock_out IS NULL
                LIMIT 1
            """, (record["employee_id"],)).fetchone()

            if open_shift:
                raise ValueError("Employee already has an active shift.")

            confirmed_arrival = now

            cursor = conn.execute("""
                INSERT INTO attendance (
                    employee_id, branch, clock_in,
                    verification_status, verified_by, verified_at,
                    gps_latitude, gps_longitude, gps_accuracy,
                    gps_distance, gps_verification_level
                )
                VALUES (?, ?, ?, 'APPROVED', ?, ?, ?, ?, ?, ?, 'GPS_EXCEPTION')
            """, (
                record["employee_id"],
                record["branch"],
                confirmed_arrival,
                manager_id,
                now,
                record["gps_latitude"],
                record["gps_longitude"],
                record["gps_accuracy"],
                record["gps_distance"]
            ))

            attendance_id = cursor.lastrowid

        cursor = conn.execute("""
            UPDATE gps_exception_requests
            SET status = ?,
                reviewed_by = ?,
                reviewed_at = ?,
                review_notes = ?,
                confirmed_arrival_at = ?,
                attendance_id = ?
            WHERE id = ? AND status = 'PENDING'
        """, (
            decision,
            manager_id,
            now,
            notes,
            confirmed_arrival,
            attendance_id,
            request_id
        ))

        if cursor.rowcount != 1:
            raise ValueError("Request was already reviewed.")

    return attendance_id
