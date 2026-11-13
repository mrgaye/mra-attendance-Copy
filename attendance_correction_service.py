"""Forgotten Clock-Out Correction System for MRA Attendance."""

from datetime import datetime, timezone

from psycopg2.errors import UniqueViolation

from database import get_connection


def _active_employee(conn, employee_id):
    employee = conn.execute("""
        SELECT id, role, branch, is_active, must_change_pin
        FROM employees
        WHERE id = ?
    """, (employee_id,)).fetchone()

    if not employee or not employee["is_active"]:
        raise ValueError("Employee account is inactive or unavailable.")

    if employee["must_change_pin"]:
        raise ValueError("Change your PIN first.")

    return employee


def get_open_shift(employee_id):
    """Return the employee's unfinished attendance shift."""

    with get_connection() as conn:
        _active_employee(conn, employee_id)

        shift = conn.execute("""
            SELECT id, employee_id, branch, clock_in, clock_out
            FROM attendance
            WHERE employee_id = ?
              AND clock_out IS NULL
            ORDER BY id DESC
            LIMIT 1
        """, (employee_id,)).fetchone()

    return dict(shift) if shift else None


def request_clock_out_correction(employee_id, requested_time, reason):
    """Submit a correction request without modifying attendance."""

    reason = (reason or "").strip()

    if not reason:
        raise ValueError("Please explain why you forgot to clock out.")

    if len(reason) > 500:
        raise ValueError("Reason must not exceed 500 characters.")

    try:
        requested = datetime.fromisoformat(requested_time)
    except (TypeError, ValueError):
        raise ValueError("Enter a valid departure date and time.")

    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=timezone.utc)

    requested = requested.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)

    if requested > now:
        raise ValueError("Departure time cannot be in the future.")

    with get_connection() as conn:
        _active_employee(conn, employee_id)

        conn.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (int(employee_id),)
        )

        shift = conn.execute("""
            SELECT id, employee_id, branch, clock_in
            FROM attendance
            WHERE employee_id = ?
              AND clock_out IS NULL
            ORDER BY id DESC
            LIMIT 1
        """, (employee_id,)).fetchone()

        if not shift:
            raise ValueError("No unfinished attendance shift found.")

        original_clock_in = datetime.fromisoformat(shift["clock_in"])

        if original_clock_in.tzinfo is None:
            original_clock_in = original_clock_in.replace(
                tzinfo=timezone.utc
            )

        if requested <= original_clock_in:
            raise ValueError(
                "Departure time must be after the original Clock-In."
            )

        existing = conn.execute("""
            SELECT id
            FROM attendance_correction_requests
            WHERE attendance_id = ?
              AND status = 'PENDING'
            LIMIT 1
        """, (shift["id"],)).fetchone()

        if existing:
            raise ValueError(
                "A correction request is already pending for this shift."
            )

        result = conn.execute("""
            INSERT INTO attendance_correction_requests
            (attendance_id, employee_id, original_clock_in,
             requested_clock_out, reason)
            VALUES (?, ?, ?, ?, ?)
        """, (
            shift["id"],
            employee_id,
            shift["clock_in"],
            requested.isoformat(),
            reason
        ))

        return result.lastrowid


def submit_clock_out_correction(employee_id, requested_time, reason):
    """Submit a request and translate duplicate errors after rollback."""
    try:
        return request_clock_out_correction(
            employee_id, requested_time, reason
        )
    except UniqueViolation as error:
        if error.diag.constraint_name != "attendance_one_pending_correction":
            raise
        raise ValueError(
            "A correction request is already pending for this shift."
        ) from error


def _authorized_correction_reviewer(conn, reviewer_id, record):
    """Validate a manager or GM reviewing an attendance correction."""

    reviewer = _active_employee(conn, reviewer_id)

    if reviewer["role"] not in ("MANAGER", "GM"):
        raise ValueError("Manager or GM access required.")

    if int(record["employee_id"]) == int(reviewer_id):
        raise ValueError("You cannot review your own correction.")

    if (
        reviewer["role"] == "MANAGER"
        and reviewer["branch"] != record["branch"]
    ):
        raise ValueError(
            "You cannot review corrections from another branch."
        )

    return reviewer


def pending_clock_out_corrections(reviewer_id):
    """Return pending correction requests visible to a manager or GM."""

    with get_connection() as conn:
        reviewer = _active_employee(conn, reviewer_id)

        if reviewer["role"] not in ("MANAGER", "GM"):
            raise ValueError("Manager or GM access required.")

        query = """
            SELECT
                c.id,
                c.attendance_id,
                c.employee_id,
                e.full_name,
                a.branch,
                c.original_clock_in,
                c.requested_clock_out,
                c.reason,
                c.requested_at,
                c.status
            FROM attendance_correction_requests c
            JOIN attendance a ON a.id = c.attendance_id
            JOIN employees e ON e.id = c.employee_id
            WHERE c.status = 'PENDING'
              AND a.clock_out IS NULL
              AND c.employee_id <> ?
        """

        parameters = [reviewer_id]

        if reviewer["role"] == "MANAGER":
            query += " AND a.branch = ?"
            parameters.append(reviewer["branch"])

        query += " ORDER BY c.requested_at ASC"

        rows = conn.execute(query, tuple(parameters)).fetchall()

    return [dict(row) for row in rows]


def review_clock_out_correction(reviewer_id, correction_id, decision, remarks=""):
    """Approve or reject a correction in one atomic transaction."""

    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError("Invalid correction decision.")

    remarks = (remarks or "").strip()
    if len(remarks) > 500:
        raise ValueError("Review remarks must not exceed 500 characters.")

    with get_connection() as conn:
        # Authenticate reviewer before obtaining locks.
        reviewer = _active_employee(conn, reviewer_id)

        if reviewer["role"] not in ("MANAGER", "GM"):
            raise ValueError("Manager or GM access required.")

        # Locate the employee whose shift is being corrected.
        reference = conn.execute("""
            SELECT employee_id
            FROM attendance_correction_requests
            WHERE id = ?
        """, (correction_id,)).fetchone()

        if not reference:
            raise ValueError("Correction request not found.")

        employee_id = int(reference["employee_id"])

        # Serialize with clock-in, clock-out and correction submission.
        conn.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (employee_id,)
        )

        # Lock the request and its attendance record.
        record = conn.execute("""
            SELECT
                c.id,
                c.employee_id,
                c.attendance_id,
                c.original_clock_in,
                c.requested_clock_out,
                c.status,
                a.branch,
                a.clock_in,
                a.clock_out,
                a.employee_id AS attendance_employee_id
            FROM attendance_correction_requests c
            JOIN attendance a ON a.id = c.attendance_id
            WHERE c.id = ?
            FOR UPDATE OF c, a
        """, (correction_id,)).fetchone()

        if not record:
            raise ValueError("Correction attendance record unavailable.")

        if int(record["employee_id"]) != employee_id:
            raise ValueError("Correction request changed. Please retry.")

        _authorized_correction_reviewer(conn, reviewer_id, record)

        if record["status"] != "PENDING":
            raise ValueError("This correction has already been reviewed.")

        if int(record["attendance_employee_id"]) != employee_id:
            raise ValueError("Correction employee does not match attendance.")

        if record["clock_out"] is not None:
            raise ValueError("This attendance shift is already closed.")

        if record["clock_in"] != record["original_clock_in"]:
            raise ValueError("Original Clock-In has changed.")

        now = datetime.now(timezone.utc)

        if decision == "APPROVED":
            clock_in = datetime.fromisoformat(record["clock_in"])
            requested = datetime.fromisoformat(
                record["requested_clock_out"]
            )

            if clock_in.tzinfo is None:
                clock_in = clock_in.replace(tzinfo=timezone.utc)

            if requested.tzinfo is None:
                requested = requested.replace(tzinfo=timezone.utc)

            clock_in = clock_in.astimezone(timezone.utc)
            requested = requested.astimezone(timezone.utc)

            if requested <= clock_in:
                raise ValueError(
                    "Requested departure must be after Clock-In."
                )

            if requested > now:
                raise ValueError(
                    "Requested departure cannot be in the future."
                )

            # Prevent the corrected shift from overlapping another shift.
            other_shifts = conn.execute("""
                SELECT id, clock_in, clock_out
                FROM attendance
                WHERE employee_id = ?
                  AND id <> ?
            """, (
                employee_id,
                record["attendance_id"]
            )).fetchall()

            for other in other_shifts:
                other_start = datetime.fromisoformat(other["clock_in"])
                if other_start.tzinfo is None:
                    other_start = other_start.replace(tzinfo=timezone.utc)
                other_start = other_start.astimezone(timezone.utc)

                if other["clock_out"] is None:
                    other_end = None
                else:
                    other_end = datetime.fromisoformat(other["clock_out"])
                    if other_end.tzinfo is None:
                        other_end = other_end.replace(tzinfo=timezone.utc)
                    other_end = other_end.astimezone(timezone.utc)

                if other_start < requested and (
                    other_end is None or other_end > clock_in
                ):
                    raise ValueError(
                        "Correction overlaps another attendance shift. "
                        "Please contact the GM."
                    )

            result = conn.execute("""
                UPDATE attendance
                SET clock_out = ?
                WHERE id = ?
                  AND employee_id = ?
                  AND clock_out IS NULL
            """, (
                requested.isoformat(),
                record["attendance_id"],
                employee_id
            ))

            if result.rowcount != 1:
                raise ValueError("Attendance could not be corrected.")

        result = conn.execute("""
            UPDATE attendance_correction_requests
            SET status = ?,
                reviewed_by = ?,
                reviewed_at = NOW(),
                review_remarks = ?,
                approved_clock_out = ?
            WHERE id = ?
              AND status = 'PENDING'
        """, (
            decision,
            reviewer_id,
            remarks,
            requested.isoformat() if decision == "APPROVED" else None,
            correction_id
        ))

        if result.rowcount != 1:
            raise ValueError("Correction was already reviewed.")

    return True


def get_employee_pending_correction(employee_id):
    """Return the pending correction for an employee's open shift."""

    with get_connection() as conn:
        _active_employee(conn, employee_id)

        record = conn.execute("""
            SELECT
                c.id,
                c.attendance_id,
                c.requested_clock_out,
                c.reason,
                c.requested_at,
                c.status,
                a.branch,
                a.clock_in
            FROM attendance_correction_requests c
            JOIN attendance a ON a.id = c.attendance_id
            WHERE c.employee_id = ?
              AND a.employee_id = ?
              AND a.clock_out IS NULL
              AND c.status = 'PENDING'
            ORDER BY c.requested_at DESC
            LIMIT 1
        """, (employee_id, employee_id)).fetchone()

    return dict(record) if record else None
