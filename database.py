import sqlite3
import os

DATABASE = os.environ.get("MRA_DATABASE", "mra_attendance.db")

def get_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def initialize_database():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                phone_number TEXT NOT NULL UNIQUE,
                pin_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (
                    role IN ('CEO', 'GM', 'MANAGER', 'STAFF')
                ),
                branch TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                must_change_pin INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                branch TEXT NOT NULL,
                clock_in TEXT NOT NULL,
                clock_out TEXT,
                verification_status TEXT NOT NULL DEFAULT 'PENDING',
                verified_by INTEGER,
                verified_at TEXT,
                gps_latitude REAL,
                gps_longitude REAL,
                gps_accuracy REAL,
                gps_distance REAL,
                gps_verification_level TEXT,
                FOREIGN KEY (employee_id) REFERENCES employees(id),
                FOREIGN KEY (verified_by) REFERENCES employees(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                destination_branch TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                approved_by INTEGER,
                FOREIGN KEY (employee_id) REFERENCES employees(id),
                FOREIGN KEY (approved_by) REFERENCES employees(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS branches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                latitude REAL,
                longitude REAL,
                gps_radius_metres INTEGER NOT NULL DEFAULT 100,
                is_active INTEGER NOT NULL DEFAULT 1,
                CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
                CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
                CHECK ((latitude IS NULL) = (longitude IS NULL)),
                CHECK (gps_radius_metres > 0),
                CHECK (is_active IN (0, 1))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS gps_exception_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                branch TEXT NOT NULL,
                request_type TEXT NOT NULL DEFAULT 'CLOCK_IN',
                requested_at TEXT NOT NULL,
                gps_latitude REAL,
                gps_longitude REAL,
                gps_accuracy REAL,
                gps_distance REAL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
                reviewed_by INTEGER,
                reviewed_at TEXT,
                review_notes TEXT,
                attendance_id INTEGER,
                confirmed_arrival_at TEXT,
                FOREIGN KEY (employee_id) REFERENCES employees(id),
                FOREIGN KEY (reviewed_by) REFERENCES employees(id),
                FOREIGN KEY (attendance_id) REFERENCES attendance(id)
            )
        """)

        # Add GPS columns to older attendance databases.
        existing_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(attendance)")
        }

        gps_columns = {
            "gps_latitude": "REAL",
            "gps_longitude": "REAL",
            "gps_accuracy": "REAL",
            "gps_distance": "REAL",
            "gps_verification_level": "TEXT",
            "clock_in_photo": "TEXT",
            "clock_out_photo": "TEXT",
        }

        for name, column_type in gps_columns.items():
            if name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE attendance ADD COLUMN {name} {column_type}"
                )

        # Allow only one pending GPS exception per employee.
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_one_pending_gps_exception_per_employee
            ON gps_exception_requests(employee_id)
            WHERE status = 'PENDING'
        """)


if __name__ == "__main__":
    initialize_database()
    print("MRA Attendance database created successfully!")
