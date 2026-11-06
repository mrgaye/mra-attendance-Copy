from pathlib import Path

from database import get_connection
from selfie_service import PHOTO_DIR


def authorized_attendance_photo(manager_id, attendance_id, action):
    """
    Return a private attendance photograph path only when
    the requesting manager is authorized to view it.
    """

    if action not in ("in", "out"):
        raise ValueError("Invalid photograph type.")

    try:
        manager_id = int(manager_id)
        attendance_id = int(attendance_id)
    except (TypeError, ValueError):
        raise ValueError("Invalid attendance photograph request.")

    if manager_id <= 0 or attendance_id <= 0:
        raise ValueError("Invalid attendance photograph request.")

    with get_connection() as conn:

        manager = conn.execute("""
            SELECT id, role, branch, is_active, must_change_pin
            FROM employees
            WHERE id = ?
        """, (manager_id,)).fetchone()

        if not manager or not manager["is_active"]:
            raise PermissionError("Manager account unavailable.")

        if manager["must_change_pin"]:
            raise PermissionError("PIN change required.")

        if manager["role"] not in ("GM", "MANAGER"):
            raise PermissionError("Manager access required.")

        record = conn.execute("""
            SELECT id, employee_id, branch,
                   clock_in_photo, clock_out_photo
            FROM attendance
            WHERE id = ?
        """, (attendance_id,)).fetchone()

        if not record:
            raise FileNotFoundError("Attendance record not found.")

        if (
            manager["role"] == "MANAGER"
            and manager["branch"] != record["branch"]
        ):
            raise PermissionError("Another branch's attendance record.")

        if record["employee_id"] == manager_id:
            raise PermissionError("Cannot review your own attendance photo.")

        filename = (
            record["clock_in_photo"]
            if action == "in"
            else record["clock_out_photo"]
        )

    if not filename:
        raise FileNotFoundError("Attendance photograph unavailable.")

    if (
        Path(filename).name != filename
        or not filename.endswith(".jpg")
        or "/" in filename
        or "\\\\" in filename
    ):
        raise ValueError("Invalid stored photograph filename.")

    directory = PHOTO_DIR.resolve()
    photo_path = (directory / filename).resolve()

    if photo_path.parent != directory:
        raise ValueError("Invalid photograph storage path.")

    if not photo_path.is_file():
        raise FileNotFoundError("Attendance photograph unavailable.")

    return photo_path
