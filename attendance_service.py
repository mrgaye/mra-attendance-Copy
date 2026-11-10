import math
from datetime import datetime, timezone
from database import get_connection

GPS_RADIUS_METRES = 100
MAX_ACCURACY_METRES = 50

BRANCHES = {
    "SHOP-01 Senegambia": None,
    "SHOP-02 Jambangjelly": None,
    "SHOP-03 BB Kotu": (13.464111, -16.703667),
}

def distance_metres(lat1, lon1, lat2, lon2):
    radius = 6371000

    a1 = math.radians(lat1)
    a2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(a1)
        * math.cos(a2)
        * math.sin(dlon / 2) ** 2
    )

    return 2 * radius * math.asin(min(1, math.sqrt(a)))


def effective_branch(employee_id, today):
    with get_connection() as conn:
        employee = conn.execute("""
            SELECT branch, is_active, must_change_pin
            FROM employees WHERE id = ?
        """, (employee_id,)).fetchone()

        if not employee or not employee["is_active"]:
            raise ValueError("Employee account is inactive.")

        if employee["must_change_pin"]:
            raise ValueError("Change your PIN first.")

        transfer = conn.execute("""
            SELECT destination_branch FROM transfers
            WHERE employee_id = ?
              AND status = 'APPROVED'
              AND start_date <= ?
              AND end_date >= ?
            ORDER BY id DESC
            LIMIT 1
        """, (employee_id, today, today)).fetchone()

        if transfer:
            return transfer["destination_branch"]

        if not employee["branch"]:
            raise ValueError("No assigned branch found.")

        return employee["branch"]


def authorized_branch(employee_id, branch, today):
    allowed = effective_branch(employee_id, today)

    if branch != allowed:
        raise ValueError(
            "Clock-in denied. Your authorized branch is " + allowed
        )

    return True


def validate_gps(branch, latitude, longitude, accuracy, allow_low_confidence=False):
    with get_connection() as conn:
        settings = conn.execute(
            """
            SELECT latitude, longitude, gps_radius_metres, is_active
            FROM branches
            WHERE name = ?
            """,
            (branch,)
        ).fetchone()

    if settings is None:
        raise ValueError("Branch is not registered.")

    if not settings["is_active"]:
        raise ValueError("This branch is inactive.")

    if settings["latitude"] is None or settings["longitude"] is None:
        raise ValueError("Branch GPS location has not been configured.")

    if any(v is None for v in (latitude, longitude, accuracy)):
        raise ValueError("Invalid GPS information.")

    if not all(math.isfinite(v) for v in
               (latitude, longitude, accuracy)):
        raise ValueError("Invalid GPS information.")

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("Invalid GPS coordinates.")

    if accuracy < 0 or accuracy > MAX_ACCURACY_METRES:
        raise ValueError(
            f"GPS accuracy is {accuracy:.1f} metres. "
            f"Required: {MAX_ACCURACY_METRES} metres or better."
        )

    distance = distance_metres(
        latitude, longitude,
        settings["latitude"], settings["longitude"]
    )

    radius = settings["gps_radius_metres"]

    if allow_low_confidence and accuracy > 50:
        if distance > radius:
            raise ValueError(
                f"Reported GPS position is {distance:.1f} metres from "
                f"the branch (allowed radius: {radius} metres; "
                f"reported accuracy: {accuracy:.1f} metres)."
            )
    elif distance + accuracy > radius:
        raise ValueError(
            f"You are outside the permitted {radius}-metre area."
        )

    return round(distance, 1)



def gps_verification_level(accuracy):
    if accuracy <= 50:
        return "STANDARD"
    return "LOW_CONFIDENCE"


def clock_in(employee_id, branch, latitude, longitude, accuracy, selfie_data):
    from selfie_service import save_selfie, delete_selfie

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    authorized_branch(employee_id, branch, today)

    distance = validate_gps(
        branch, latitude, longitude, accuracy,
        allow_low_confidence=False
    )

    filename = None

    try:
        with get_connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (int(employee_id),))

            open_shift = conn.execute("""
                SELECT id FROM attendance
                WHERE employee_id = ?
                AND clock_out IS NULL
                LIMIT 1
            """, (employee_id,)).fetchone()

            if open_shift:
                raise ValueError("You are already clocked in.")

            filename = save_selfie(selfie_data, "in")

            conn.execute("""
                INSERT INTO attendance
                (employee_id, branch, clock_in,
                 verification_status, gps_latitude,
                 gps_longitude, gps_accuracy, gps_distance,
                 gps_verification_level, clock_in_photo)
                VALUES (?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?)
            """, (
                employee_id,
                branch,
                now.isoformat(),
                latitude,
                longitude,
                accuracy,
                distance,
                gps_verification_level(accuracy),
                filename
            ))

            conn.commit()

    except Exception:
        if filename:
            delete_selfie(filename)
        raise

    return distance


def clock_out(employee_id, latitude, longitude, accuracy, selfie_data):
    from selfie_service import save_selfie, delete_selfie

    now = datetime.now(timezone.utc)
    filename = None

    try:
        with get_connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (int(employee_id),))

            shift = conn.execute("""
                SELECT id, branch FROM attendance
                WHERE employee_id = ?
                AND clock_out IS NULL
                ORDER BY id DESC
                LIMIT 1
            """, (employee_id,)).fetchone()

            if not shift:
                raise ValueError("No active shift found.")

            validate_gps(
                shift["branch"],
                latitude,
                longitude,
                accuracy
            )

            filename = save_selfie(selfie_data, "out")

            cursor = conn.execute("""
                UPDATE attendance
                SET clock_out = ?, clock_out_photo = ?
                WHERE id = ? AND clock_out IS NULL
            """, (now.isoformat(), filename, shift["id"]))

            if cursor.rowcount != 1:
                raise ValueError("Unable to close attendance shift.")

            conn.commit()

    except Exception:
        if filename:
            delete_selfie(filename)
        raise

    return True
