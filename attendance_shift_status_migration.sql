-- MRA Attendance: Shift Status Migration
-- DEVELOPMENT TESTING FIRST. Do not execute against production.

BEGIN;

ALTER TABLE attendance
ADD COLUMN shift_status TEXT DEFAULT 'ACTIVE';

UPDATE attendance
SET shift_status = CASE
    WHEN clock_out IS NOT NULL THEN 'CLOSED'
    WHEN EXISTS (
        SELECT 1
        FROM attendance_correction_requests c
        WHERE c.attendance_id = attendance.id
          AND c.status = 'PENDING'
    ) THEN 'PENDING_CORRECTION'
    ELSE 'ACTIVE'
END;

ALTER TABLE attendance
ALTER COLUMN shift_status SET NOT NULL;

ALTER TABLE attendance
ADD CONSTRAINT attendance_shift_status_check
CHECK (shift_status IN ('ACTIVE', 'PENDING_CORRECTION', 'CLOSED'));

ALTER TABLE attendance
ADD CONSTRAINT attendance_shift_clock_out_check
CHECK (
    (shift_status = 'CLOSED' AND clock_out IS NOT NULL)
    OR
    (shift_status IN ('ACTIVE', 'PENDING_CORRECTION')
     AND clock_out IS NULL)
);

-- Replace the previous restriction on all unfinished shifts.
-- Only one ACTIVE shift is allowed per employee.
-- PENDING_CORRECTION shifts must not block new clock-ins.
DROP INDEX IF EXISTS attendance_one_open_shift_per_employee;

CREATE UNIQUE INDEX attendance_one_active_shift_per_employee
ON attendance(employee_id)
WHERE shift_status = 'ACTIVE';

COMMIT;
