from datetime import date
from database import get_connection


def attendance_report(viewer_id, report_date=None):
    if report_date is None:
        report_date = date.today().isoformat()

    try:
        date.fromisoformat(report_date)
    except ValueError:
        raise ValueError("Invalid report date.")

    with get_connection() as conn:
        viewer = conn.execute("""
            SELECT id, role, branch, is_active, must_change_pin
            FROM employees
            WHERE id = ?
        """, (viewer_id,)).fetchone()

        if not viewer or not viewer["is_active"]:
            raise ValueError("Account unavailable.")

        if viewer["must_change_pin"]:
            raise ValueError("PIN change required.")

        if viewer["role"] not in ("GM", "MANAGER"):
            raise ValueError("Reporting access required.")

        params = [report_date + "%"]

        branch_filter = ""

        if viewer["role"] == "MANAGER":
            branch_filter = "AND a.branch = ?"
            params.append(viewer["branch"])

        records = conn.execute(f"""
            SELECT
                a.id,
                e.full_name,
                a.branch,
                a.clock_in,
                a.clock_out,
                a.verification_status
            FROM attendance a
            JOIN employees e
              ON e.id = a.employee_id
            WHERE a.clock_in LIKE ?
              {branch_filter}
            ORDER BY a.clock_in DESC
        """, params).fetchall()

    records = [dict(record) for record in records]

    summary = {
        "total": len(records),
        "approved": 0,
        "pending": 0,
        "rejected": 0,
        "clocked_in": 0
    }

    for record in records:
        status = (record["verification_status"] or "").upper()

        if status == "APPROVED":
            summary["approved"] += 1
        elif status == "PENDING":
            summary["pending"] += 1
        elif status == "REJECTED":
            summary["rejected"] += 1

        if record["clock_out"] is None:
            summary["clocked_in"] += 1

    return summary, records


def advanced_attendance_report(
    viewer_id,
    start_date,
    end_date,
    branch=None,
    employee_id=None
):
    from datetime import datetime, date, timedelta

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except (ValueError, TypeError):
        raise ValueError("Invalid report date range.")

    if end < start:
        raise ValueError("End date cannot be before start date.")

    if (end - start).days > 366:
        raise ValueError("Report range cannot exceed 366 days.")

    with get_connection() as conn:
        viewer = conn.execute("""
            SELECT id, role, branch, is_active, must_change_pin
            FROM employees
            WHERE id = ?
        """, (viewer_id,)).fetchone()

        if not viewer or not viewer["is_active"]:
            raise ValueError("Account unavailable.")

        if viewer["must_change_pin"]:
            raise ValueError("PIN change required.")

        if viewer["role"] not in ("GM", "MANAGER"):
            raise ValueError("Reporting access required.")

        conditions = [
            "a.clock_in >= ?",
            "a.clock_in < ?"
        ]

        params = [
            start.isoformat(),
            (end + timedelta(days=1)).isoformat()
        ]

        if viewer["role"] == "MANAGER":
            conditions.append("a.branch = ?")
            params.append(viewer["branch"])
        elif branch:
            conditions.append("a.branch = ?")
            params.append(branch)

        if employee_id:
            conditions.append("a.employee_id = ?")
            params.append(employee_id)

        where_clause = " AND ".join(conditions)

        records = conn.execute(f"""
            SELECT
                a.id,
                a.employee_id,
                e.full_name,
                a.branch,
                a.clock_in,
                a.clock_out,
                a.verification_status
            FROM attendance a
            JOIN employees e
              ON e.id = a.employee_id
            WHERE {where_clause}
            ORDER BY a.clock_in DESC
        """, params).fetchall()

    records = [dict(record) for record in records]

    summary = {
        "total": len(records),
        "approved": 0,
        "pending": 0,
        "rejected": 0,
        "open_shifts": 0,
        "completed_shifts": 0,
        "working_hours": 0.0,
        "unique_employees": 0
    }

    employee_ids = set()
    total_seconds = 0

    for record in records:
        employee_ids.add(record["employee_id"])

        status = (
            record["verification_status"] or ""
        ).upper()

        if status == "APPROVED":
            summary["approved"] += 1
        elif status == "PENDING":
            summary["pending"] += 1
        elif status == "REJECTED":
            summary["rejected"] += 1

        record["hours_worked"] = None

        if record["clock_out"] is None:
            summary["open_shifts"] += 1
            continue

        try:
            clock_in = datetime.fromisoformat(
                record["clock_in"]
            )

            clock_out = datetime.fromisoformat(
                record["clock_out"]
            )

            duration = (
                clock_out - clock_in
            ).total_seconds()

            if duration >= 0:
                record["hours_worked"] = round(
                    duration / 3600, 2
                )

                summary["completed_shifts"] += 1
                total_seconds += duration

        except (ValueError, TypeError):
            pass

    summary["unique_employees"] = len(employee_ids)
    summary["working_hours"] = round(
        total_seconds / 3600, 2
    )

    return summary, records


def resolve_report_period(
    period="daily",
    selected_date=None,
    start_date=None,
    end_date=None
):
    import calendar
    from datetime import date, timedelta

    if period not in ("daily", "weekly", "monthly", "custom"):
        raise ValueError("Invalid reporting period.")

    try:
        selected = (
            date.fromisoformat(selected_date)
            if selected_date
            else date.today()
        )

        if period == "daily":
            start = end = selected

        elif period == "weekly":
            start = selected - timedelta(
                days=selected.weekday()
            )
            end = start + timedelta(days=6)

        elif period == "monthly":
            start = selected.replace(day=1)
            last_day = calendar.monthrange(
                selected.year,
                selected.month
            )[1]
            end = selected.replace(day=last_day)

        else:
            if not start_date or not end_date:
                raise ValueError(
                    "Custom reports require start and end dates."
                )

            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)

    except (TypeError, ValueError) as error:
        raise ValueError(
            "Invalid report date or date range."
        ) from error

    if end < start:
        raise ValueError(
            "End date cannot be before start date."
        )

    if (end - start).days > 366:
        raise ValueError(
            "Report range cannot exceed 366 days."
        )

    return start.isoformat(), end.isoformat()
