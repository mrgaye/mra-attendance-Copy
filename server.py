from fastapi.responses import Response
from attendance_photo_access import authorized_attendance_photo
from fastapi.staticfiles import StaticFiles
from gps_exception_service import (
    request_gps_exception,
    pending_gps_exceptions,
    review_gps_exception
)
from csrf_service import csrf_input, validate_csrf

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from pathlib import Path
import os
import secrets
from html import escape

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from auth import authenticate
from database import get_connection, initialize_database
from psycopg2.errors import UniqueViolation

initialize_database()


mra_login_limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[]
)

app = FastAPI(title="MRA Staff Attendance")

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.middleware("http")
async def pwa_service_worker_scope(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/static/sw.js":
        response.headers["Service-Worker-Allowed"] = "/"
    return response

# Development-only session key. Sessions reset when server restarts.
app.add_middleware(
    SessionMiddleware,
    secret_key=(
        os.environ.get("MRA_SESSION_SECRET")
        or Path(__file__).with_name(".session_secret").read_text().strip()
    ),
    same_site="lax",
    https_only=os.environ.get("MRA_PRODUCTION") == "1",
    max_age=3600
)

BRANCHES = [
    "SHOP-01 Senegambia",
    "SHOP-02 Jambangjelly",
    "SHOP-03 BB Kotu"
]

def page(content):
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport"
              content="width=device-width, initial-scale=1">
        <title>MRA Staff Attendance</title>
        <link rel="manifest" href="/static/manifest.json">
        <link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">
        <meta name="theme-color" content="#163b65">
        <meta name="mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-status-bar-style" content="default">
        <meta name="apple-mobile-web-app-title" content="MRA Attendance">
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                font-family: Arial, sans-serif;
                background: #f2f5fa;
                margin: 0;
                padding: 24px;
                color: #243247;
            }}
            .card {{
                background: white;
                padding: 30px;
                border-radius: 16px;
                max-width: 460px;
                margin: 55px auto;
                box-shadow: 0 5px 25px #00000012;
            }}
            h1 {{ color: #17365d; font-size: 25px; }}
            label {{ display: block; margin-top: 18px; }}
            input {{
                width: 100%;
                padding: 13px;
                margin-top: 7px;
                border: 1px solid #ccd3df;
                border-radius: 8px;
                font-size: 16px;
            }}
            button {{
                width: 100%;
                padding: 14px;
                margin-top: 24px;
                border: none;
                border-radius: 8px;
                background: #17365d;
                color: white;
                font-size: 16px;
                cursor: pointer;
            }}
            .error {{ color: #b42318; }}
            .muted {{ color: #667085; }}
            li {{ margin: 12px 0; }}
            a {{ color: #17365d; }}
        </style>
    </head>
    <body>{content}
        <script>
            if ("serviceWorker" in navigator) {{
                window.addEventListener("load", function() {{
                    navigator.serviceWorker.register("/static/sw.js", {{ scope: "/" }})
                        .catch(function(error) {{
                            console.error("Service worker registration failed:", error);
                        }});
                }});
            }}
        </script>
        <script>
            document.addEventListener("submit", function(event) {{
                const form = event.target;

                if (!(form instanceof HTMLFormElement)) return;
                if (form.method.toLowerCase() !== "post") return;

                if (["clockin", "clockout", "gps-exception"]
                    .includes(form.id)) return;
                if (form.getAttribute("action") === "/login") return;

                if (event.defaultPrevented) return;

                if (form.dataset.submitting === "true") {{
                    event.preventDefault();
                    return;
                }}

                const submitter = event.submitter;

                if (submitter && submitter.disabled) {{
                    event.preventDefault();
                    return;
                }}

                form.dataset.submitting = "true";

                const buttons = form.querySelectorAll(
                    'button[type="submit"], input[type="submit"]'
                );

                buttons.forEach(function(button) {{
                    button.setAttribute("aria-busy", "true");
                    button.style.pointerEvents = "none";
                }});

                if (submitter && submitter.tagName === "BUTTON") {{
                    submitter.dataset.originalText =
                        submitter.textContent.trim();

                    submitter.textContent = "Processing... Please wait";
                }}
            }});

            window.addEventListener("pageshow", function() {{
                document.querySelectorAll(
                    'form[data-submitting="true"]'
                ).forEach(function(form) {{
                    delete form.dataset.submitting;

                    form.querySelectorAll(
                        'button[type="submit"], input[type="submit"]'
                    ).forEach(function(button) {{
                        button.removeAttribute("aria-busy");
                        button.style.pointerEvents = "";
                        if (button.dataset.originalText) {{
                            button.textContent =
                                button.dataset.originalText;
                        }}
                    }});
                }});
            }});
        </script>
    </body>
    </html>
    """

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if request.session.get("employee_id"):
        return RedirectResponse("/dashboard", status_code=303)

    return login_page(request)

def login_page(request, error=""):
    message = (
        f'<p class="error">{escape(error)}</p>'
        if error else ""
    )
    return HTMLResponse(page(f"""
        <div class="card">
            <h1>MRA Staff Attendance</h1>
            <p class="muted">Employee Login</p>
            {message}
            <form action="/login" method="post">
                {csrf_input(request)}
                <label>Phone Number</label>
                <input name="phone" type="tel"
                       placeholder="+220 XXXXXXX"
                       autocomplete="username" required>

                <label>6-Digit PIN</label>
                <input name="pin" type="password"
                       inputmode="numeric"
                       minlength="6" maxlength="6"
                       autocomplete="current-password"
                       required>

                <button type="submit" id="login-submit">Sign In</button>
            </form>
            <script>
                document.querySelector('form[action="/login"]')
                    .addEventListener("submit", function(event) {{
                        const button = document.getElementById("login-submit");
                        if (button.disabled) {{
                            event.preventDefault();
                            return;
                        }}
                        button.disabled = true;
                        button.textContent = "Signing in... Please wait";
                        button.setAttribute("aria-busy", "true");
                    }});
            </script>
        </div>
    """))

@app.post("/login")
@mra_login_limiter.limit("5/minute")
async def login(request: Request,
          phone: str = Form(...),
          pin: str = Form(...)):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))

    try:
        employee = authenticate(phone, pin)
    except ValueError:
        employee = None

    if not employee:
        return login_page(request, "Invalid phone number or PIN.")

    request.session.clear()
    request.session["employee_id"] = employee["id"]
    request.session["auth_version"] = employee["auth_version"]

    return RedirectResponse("/dashboard", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    employee_id = request.session.get("employee_id")

    if not employee_id:
        return RedirectResponse("/", status_code=303)

    with get_connection() as conn:
        employee = conn.execute("""
            SELECT id, full_name, role, branch,
                   is_active, must_change_pin
            FROM employees
            WHERE id = ?
        """, (employee_id,)).fetchone()

        if not employee or not employee["is_active"]:
            request.session.clear()
            return RedirectResponse("/", status_code=303)

        if employee["must_change_pin"]:
            return RedirectResponse("/change-pin", status_code=303)

        # Direct each management role to its dedicated dashboard.
        if employee["role"] == "GM":
            return RedirectResponse("/gm/dashboard", status_code=303)

        if employee["role"] == "MANAGER":
            return RedirectResponse("/manager/dashboard", status_code=303)

        latest = conn.execute("""
            SELECT branch, clock_in, clock_out,
                   verification_status
            FROM attendance
            WHERE employee_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (employee_id,)).fetchone()

        active = conn.execute("""
            SELECT branch, clock_in, verification_status
            FROM attendance
            WHERE employee_id = ?
              AND clock_out IS NULL
            ORDER BY id DESC
            LIMIT 1
        """, (employee_id,)).fetchone()

    name = escape(employee["full_name"])
    role = escape(employee["role"])
    branch = escape(employee["branch"] or "All Branches")

    def local_time(value):
        if not value:
            return "Not available"
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(
                ZoneInfo("Africa/Banjul")
            ).strftime("%d %b %Y, %I:%M %p")
        except (ValueError, TypeError, OverflowError):
            return "Unavailable"

    status = "Clocked In" if active else "Clocked Out"
    status_class = "working" if active else "off-duty"

    clock_in_time = (
        local_time(active["clock_in"])
        if active else "Not currently clocked in"
    )

    verification = (
        escape(latest["verification_status"] or "UNKNOWN")
        if latest else "No attendance yet"
    )

    hours = "0h 00m"

    if active:
        try:
            started = datetime.fromisoformat(active["clock_in"])
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)

            seconds = max(
                0,
                int((datetime.now(timezone.utc) - started).total_seconds())
            )
            hours = f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"
        except (ValueError, TypeError, OverflowError):
            hours = "Unavailable"

    navigation = [
        ("/attendance", "GPS Attendance", "Clock in, clock out and history"),
    ]

    if role == "GM":
        navigation.extend([
            ("/manager/dashboard", "Manager Dashboard", "Branch attendance overview"),
            ("/employees", "Employee Management", "Manage staff accounts"),
            ("/branches", "Branch Management", "Manage branch locations"),
            ("/transfers", "Branch Transfers", "Manage temporary assignments"),
            ("/reports", "Attendance Reports", "Review attendance records"),
            ("/manager/attendance", "Manager Verification", "Review attendance"),
            ("/manager/gps-exceptions", "GPS Exceptions", "Review GPS requests"),
            ("/manager/clockout-corrections", "Clock-Out Corrections", "Review forgotten clock-out requests"),
        ])
    elif role == "MANAGER":
        navigation.extend([
            ("/manager/dashboard", "Manager Dashboard", "Branch attendance overview"),
            ("/manager/attendance", "Manager Verification", "Review attendance"),
            ("/manager/gps-exceptions", "GPS Exceptions", "Review GPS requests"),
            ("/manager/clockout-corrections", "Clock-Out Corrections", "Review forgotten clock-out requests"),
        ])

    links = "".join(
        f'<a class="mra-link" href="{url}">'
        f'<strong>{escape(label)}</strong>'
        f'<span>{escape(description)}</span>'
        f'</a>'
        for url, label, description in navigation
    )

    return HTMLResponse(page(f"""
        <style>
            .mra-dashboard {{
                max-width: 960px;
                margin: 0 auto;
                font-family: Arial, sans-serif;
            }}
            .mra-header {{
                background: #123b56;
                color: white;
                padding: 28px;
                border-radius: 16px;
                margin-bottom: 18px;
            }}
            .mra-header h1 {{
                margin: 8px 0;
                color: white;
            }}
            .mra-header p {{
                color: #e0edf5;
            }}
            .mra-grid {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 14px;
                margin-bottom: 20px;
            }}
            .mra-stat {{
                background: white;
                border: 1px solid #dce5ed;
                border-radius: 12px;
                padding: 18px;
                min-width: 0;
            }}
            .mra-stat small {{
                color: #627487;
                display: block;
                margin-bottom: 10px;
            }}
            .mra-stat strong {{
                color: #18384e;
                display: block;
                overflow-wrap: anywhere;
            }}
            .working {{ color: #16764a !important; }}
            .off-duty {{ color: #a65d12 !important; }}
            .mra-action {{
                display: block;
                text-align: center;
                padding: 17px;
                background: #16764a;
                color: white !important;
                border-radius: 12px;
                text-decoration: none;
                font-weight: bold;
                margin: 18px 0;
            }}
            .mra-links {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 12px;
            }}
            .mra-link {{
                display: block;
                padding: 18px;
                background: #edf3f8;
                border-radius: 12px;
                text-decoration: none;
                color: #123b56;
            }}
            .mra-link strong,
            .mra-link span {{
                display: block;
            }}
            .mra-link span {{
                font-size: 13px;
                color: #526b7d;
                margin-top: 6px;
            }}
            .mra-logout {{
                margin-top: 24px;
            }}
            @media (max-width: 600px) {{
                .mra-grid, .mra-links {{
                    grid-template-columns: 1fr;
                }}
                .mra-header {{ padding: 20px; }}
            }}
        
            .mra-header {{
                background: #123B56;
                color: #FFFFFF;
                border-bottom: 5px solid #EF4655;
                position: relative;
                padding: 24px 24px 24px 125px;
                min-height: 105px;
                box-sizing: border-box;
            }}
            .mra-header h1 {{
                color: #FFFFFF;
            }}
            .mra-header p {{
                color: #E5EDF4;
            }}
            .mra-logo {{
                position: absolute;
                left: 20px;
                top: 50%;
                transform: translateY(-50%);
                width: 86px;
                height: 86px;
                object-fit: contain;
                background: #FFFFFF;
                border-radius: 50%;
                padding: 5px;
                box-sizing: border-box;
            }}
            @media (max-width: 600px) {{
                .mra-header {{
                    padding: 20px 14px 20px 90px;
                }}
                .mra-logo {{
                    width: 62px;
                    height: 62px;
                    left: 14px;
                }}
            }}

            .mra-header {{
                position: relative;
            }}
            .mra-logout {{
                position: absolute;
                top: 18px;
                right: 20px;
                margin: 0;
                z-index: 2;
            }}
            .mra-logout button {{
                background: #EF4655;
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 10px 18px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
            }}
            .mra-logout button:hover {{
                background: #D93647;
            }}
            @media (max-width: 600px) {{
                .mra-logout {{
                    position: static;
                    margin-bottom: 15px;
                    text-align: right;
                }}
            }}
</style>

        <div class="mra-dashboard">
            <div class="mra-header">
<form class="mra-logout" action="/logout" method="post">
                {csrf_input(request)}
                <button type="submit">Log Out</button>
            </form>
                <img
                    src="/static/images/mra-logo.jpeg"
                    alt="MRA Trading Company Limited Logo"
                    class="mra-logo"
                >
                <small>MRA TRADING COMPANY LIMITED</small>
                <h1>Staff Dashboard</h1>
                <h2>Welcome, {name}</h2>
                <p>{role} | {branch}</p>
            </div>

            <div class="mra-grid">
                <div class="mra-stat">
                    <small>Current Shift</small>
                    <strong class="{status_class}">{status}</strong>
                </div>
                <div class="mra-stat">
                    <small>Latest Verification</small>
                    <strong>{verification}</strong>
                </div>
                <div class="mra-stat">
                    <small>Clock-in Time</small>
                    <strong>{clock_in_time}</strong>
                </div>
                <div class="mra-stat">
                    <small>Current Shift Duration</small>
                    <strong>{hours}</strong>
                </div>
            </div>

            <a class="mra-action" href="/attendance">
                Open GPS Attendance
            </a>

            <h2>Quick Access</h2>
            <div class="mra-links">
                {links}
            </div>

            
        </div>
    """))


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    validate_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

# EMPLOYEE MANAGEMENT

from employee_service import register_employee, list_employees, update_employee, deactivate_employee, reactivate_employee, reset_employee_pin

from fastapi import HTTPException

def require_gm(request):
    employee_id = request.session.get("employee_id")

    if not employee_id:
        raise HTTPException(status_code=401, detail="Login required")

    with get_connection() as conn:
        employee = conn.execute("""
            SELECT id, role, is_active
            FROM employees
            WHERE id = ?
        """, (employee_id,)).fetchone()

    if not employee or not employee["is_active"]:
        raise HTTPException(status_code=403, detail="Access denied")

    if employee["role"] != "GM":
        raise HTTPException(status_code=403, detail="GM access required")

    return employee



@app.post("/employees/update")
async def update_employee_route(request: Request):
    require_gm(request)

    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))

    try:
        employee_id = int(form.get("employee_id", ""))
        name = str(form.get("name", ""))
        phone = str(form.get("phone", ""))
        role = str(form.get("role", ""))
        branch = str(form.get("branch", ""))

        update_employee(
            employee_id,
            name,
            phone,
            role,
            branch
        )

    except (ValueError, TypeError) as exc:
        return page(f"""
            <h2>Employee Update Failed</h2>
            <p>{escape(str(exc))}</p>
            <a href="/employees">Back to Employee Management</a>
        """)

    return RedirectResponse(
        "/employees",
        status_code=303
    )



@app.post("/employees/deactivate")
async def deactivate_employee_route(request: Request):
    require_gm(request)

    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))

    try:
        employee_id = int(form.get("employee_id", ""))
        deactivate_employee(employee_id)

    except (ValueError, TypeError) as exc:
        return HTMLResponse(page(f"""
            <div class="card">
                <h2>Employee Deactivation Failed</h2>
                <p>{escape(str(exc))}</p>
                <a href="/employees">Back to Employee Management</a>
            </div>
        """), status_code=400)

    return RedirectResponse(
        "/employees",
        status_code=303
    )



@app.post("/employees/reactivate")
async def reactivate_employee_route(request: Request):
    require_gm(request)

    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))

    try:
        employee_id = int(form.get("employee_id", ""))
        reactivate_employee(employee_id)

    except (ValueError, TypeError) as exc:
        return HTMLResponse(page(f"""
            <div class="card">
                <h2>Employee Reactivation Failed</h2>
                <p>{escape(str(exc))}</p>
                <a href="/employees">Back to Employee Management</a>
            </div>
        """), status_code=400)

    return RedirectResponse(
        "/employees",
        status_code=303
    )



@app.post("/employees/reset-pin")
async def reset_employee_pin_route(request: Request):
    require_gm(request)

    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))

    try:
        employee_id = int(form.get("employee_id", ""))
        temporary_pin = reset_employee_pin(employee_id)

    except (ValueError, TypeError) as exc:
        return HTMLResponse(page(f"""
            <div class="card">
                <h2>PIN Reset Failed</h2>
                <p>{escape(str(exc))}</p>
                <a href="/employees">Back to Employee Management</a>
            </div>
        """), status_code=400)

    return HTMLResponse(
        page(f"""
            <div class="card">
                <h2>Employee PIN Reset Successful</h2>

                <p>
                    A temporary PIN has been generated.
                    Give it securely to the employee.
                </p>

                <p><strong>Temporary PIN:</strong></p>

                <h2>{escape(temporary_pin)}</h2>

                <p>
                    The employee must change this PIN
                    after logging in.
                </p>

                <p>
                    This PIN will not be displayed again.
                    Do not share it in group chats.
                </p>

                <a href="/employees">
                    Back to Employee Management
                </a>
            </div>
        """),
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
        }
    )


@app.get("/employees", response_class=HTMLResponse)
def employees_page(request: Request):
    require_gm(request)

    search = request.query_params.get("search", "").strip()
    branch_filter = request.query_params.get("branch", "")
    role_filter = request.query_params.get("role", "")
    status_filter = request.query_params.get("status", "")

    employees = list_employees(
        search=search,
        branch=branch_filter,
        role=role_filter,
        status=status_filter,
    )

    rows = ""

    for employee in employees:
        status = "Active" if employee["is_active"] else "Inactive"

        edit_action = ""

        if employee["role"] in ("STAFF", "MANAGER"):
            edit_branch_options = "".join(
                f'<option value="{escape(b, quote=True)}"'
                f'{" selected" if employee["branch"] == b else ""}>'
                f'{escape(b)}</option>'
                for b in BRANCHES
            )

            edit_role_options = "".join(
                f'<option value="{r}"'
                f'{" selected" if employee["role"] == r else ""}>'
                f'{label}</option>'
                for r, label in (
                    ("STAFF", "Staff"),
                    ("MANAGER", "Branch Manager"),
                )
            )

            edit_action = f"""
            <details>
                <summary style="cursor:pointer">Edit Employee</summary>

                <form action="/employees/update" method="post">
                    {csrf_input(request)}

                    <input type="hidden" name="employee_id"
                           value="{employee['id']}">

                    <label>Full Name</label>
                    <input name="name" required maxlength="150"
                           value="{escape(employee['full_name'], quote=True)}">

                    <label>Phone Number</label>
                    <input name="phone" type="tel" required
                           value="{escape(employee['phone_number'], quote=True)}">

                    <label>Role</label>
                    <select name="role" required>
                        {edit_role_options}
                    </select>

                    <label>Assigned Branch</label>
                    <select name="branch" required>
                        {edit_branch_options}
                    </select>

                    <button type="submit">Save Changes</button>
                </form>
            </details>
            """

        deactivate_action = ""

        if (
            employee["role"] in ("STAFF", "MANAGER")
            and employee["is_active"]
        ):
            deactivate_action = f"""
            <form action="/employees/deactivate"
                  method="post"
                  onsubmit="return confirm('Deactivate this employee?');"
                  style="margin-top:10px">
                {csrf_input(request)}

                <input type="hidden"
                       name="employee_id"
                       value="{employee['id']}">

                <button type="submit"
                        style="background:#EF4655;color:white">
                    Deactivate
                </button>
            </form>
            """

        reactivate_action = ""

        if (
            employee["role"] in ("STAFF", "MANAGER")
            and not employee["is_active"]
        ):
            reactivate_action = f"""
            <form action="/employees/reactivate"
                  method="post"
                  onsubmit="return confirm('Reactivate this employee?');"
                  style="margin-top:10px">
                {csrf_input(request)}

                <input type="hidden"
                       name="employee_id"
                       value="{employee['id']}">

                <button type="submit"
                        style="background:#16764a;color:white">
                    Reactivate
                </button>
            </form>
            """

        reset_pin_action = ""

        if (
            employee["role"] in ("STAFF", "MANAGER")
            and employee["is_active"]
        ):
            reset_pin_action = f"""
            <form action="/employees/reset-pin"
                  method="post"
                  onsubmit="return confirm('Reset this employee PIN? Their existing sessions will be invalidated.');"
                  style="margin-top:10px">
                {csrf_input(request)}

                <input type="hidden"
                       name="employee_id"
                       value="{employee['id']}">

                <button type="submit"
                        style="background:#123B56;color:white">
                    Reset PIN
                </button>
            </form>
            """

        rows += f"""
        <tr>
            <td>{escape(employee['full_name'])}</td>
            <td>{escape(employee['phone_number'])}</td>
            <td>{escape(employee['role'])}</td>
            <td>{escape(employee['branch'] or 'All Branches')}</td>
            <td>{status}</td>
            <td>{edit_action}{reset_pin_action}{deactivate_action}{reactivate_action}</td>
        </tr>
        """

    branch_options = "".join(
        f'<option value="{escape(b)}">{escape(b)}</option>'
        for b in BRANCHES
    )

    filter_branch_options = "".join(
        f'<option value="{escape(b, quote=True)}"'
        f'{" selected" if branch_filter == b else ""}>'
        f'{escape(b)}</option>'
        for b in BRANCHES
    )

    return HTMLResponse(page(f"""
        <div class="card" style="max-width:850px">
            <h1>Employee Management</h1>
            <p>Register and manage MRA employees.</p>

            <h3>Register New Employee</h3>

            <form action="/employees/register" method="post">
                {csrf_input(request)}
                <label>Full Name</label>
                <input name="name" required maxlength="150">

                <label>Phone Number</label>
                <input name="phone" type="tel"
                       placeholder="+220 XXXXXXX" required>

                <label>Employee Role</label>
                <select name="role" required
                        style="width:100%;padding:13px;margin-top:7px">
                    <option value="STAFF">Staff</option>
                    <option value="MANAGER">Branch Manager</option>
                </select>

                <label>Assigned Branch</label>
                <select name="branch" required
                        style="width:100%;padding:13px;margin-top:7px">
                    {branch_options}
                </select>

                <button type="submit">Register Employee</button>
            </form>

            <h3>Registered Employees</h3>

            <form action="/employees" method="get">
                <label>Search Employee</label>
                <input type="search" name="search"
                       placeholder="Name or phone number"
                       value="{escape(search, quote=True)}">

                <label>Branch</label>
                <select name="branch">
                    <option value="">All Branches</option>
                    {filter_branch_options}
                </select>

                <label>Role</label>
                <select name="role">
                    <option value="">All Roles</option>
                    <option value="STAFF"
                        {"selected" if role_filter == "STAFF" else ""}>Staff</option>
                    <option value="MANAGER"
                        {"selected" if role_filter == "MANAGER" else ""}>Manager</option>
                </select>

                <label>Status</label>
                <select name="status">
                    <option value="">All Statuses</option>
                    <option value="active"
                        {"selected" if status_filter == "active" else ""}>Active</option>
                    <option value="inactive"
                        {"selected" if status_filter == "inactive" else ""}>Inactive</option>
                </select>

                <button type="submit">Search Employees</button>
                <p><a href="/employees">Clear Filters</a></p>
            </form>

            <p>Employees found: {len(employees)}</p>

            <div style="overflow-x:auto">
                <table border="1" cellpadding="10"
                       style="border-collapse:collapse;width:100%">
                    <tr>
                        <th>Name</th>
                        <th>Phone</th>
                        <th>Role</th>
                        <th>Branch</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                    {rows}
                </table>
            </div>

            <p><a href="/dashboard">Back to Dashboard</a></p>
        </div>
    """))


@app.post("/employees/register", response_class=HTMLResponse)
async def register_employee_route(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    role: str = Form(...),
    branch: str = Form(...)
):
    require_gm(request)
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))

    try:
        employee_id, temporary_pin = register_employee(
            name, phone, role, branch
        )
    except ValueError as error:
        return HTMLResponse(
            page(f"""
                <div class="card">
                    <h2>Registration Failed</h2>
                    <p class="error">{escape(str(error))}</p>
                    <a href="/employees">Try Again</a>
                </div>
            """),
            status_code=400
        )

    return HTMLResponse(page(f"""
        <div class="card">
            <h1>Employee Registered!</h1>

            <p><strong>Name:</strong> {escape(name)}</p>
            <p><strong>Phone:</strong> {escape(phone)}</p>
            <p><strong>Role:</strong> {escape(role)}</p>
            <p><strong>Branch:</strong> {escape(branch)}</p>

            <h3>Temporary PIN</h3>
            <h1>{temporary_pin}</h1>

            <p class="muted">
                Give this PIN privately to the employee.
                It will not be displayed again.
                The employee must change it at first login
                once that feature is implemented.
            </p>

            <a href="/employees">Back to Employee Management</a>
        </div>
    """))

# FIRST-LOGIN PIN CHANGE

from pin_service import change_pin

@app.middleware("http")
async def enforce_pin_change(request: Request, call_next):
    employee_id = request.session.get("employee_id")

    if employee_id:
        with get_connection() as conn:
            employee = conn.execute("""
                SELECT is_active, must_change_pin, auth_version
                FROM employees
                WHERE id = ?
            """, (employee_id,)).fetchone()

        if not employee or not employee["is_active"]:
            request.session.clear()
            return RedirectResponse("/", status_code=303)

        session_version = request.session.get("auth_version")

        if (
            type(session_version) is not int
            or session_version != employee["auth_version"]
        ):
            request.session.clear()
            return RedirectResponse("/", status_code=303)

        allowed_paths = {
            "/change-pin",
            "/logout",
        }

        if (
            employee["must_change_pin"]
            and request.url.path not in allowed_paths
        ):
            if request.method in ("GET", "HEAD"):
                return RedirectResponse(
                    "/change-pin",
                    status_code=303
                )

            raise HTTPException(
                status_code=403,
                detail="Change your PIN before continuing."
            )

    return await call_next(request)


@app.get("/change-pin", response_class=HTMLResponse)
def change_pin_page(request: Request):
    if not request.session.get("employee_id"):
        return RedirectResponse("/", status_code=303)

    return HTMLResponse(page(f"""
        <div class="card">
            <h1>Change Your PIN</h1>
            <p class="muted">
                For security, you must change your
                temporary PIN before continuing.
            </p>

            <form action="/change-pin" method="post">
                {csrf_input(request)}
                <label>Current PIN</label>
                <input name="current_pin" type="password"
                       inputmode="numeric" maxlength="6"
                       required>

                <label>New 6-Digit PIN</label>
                <input name="new_pin" type="password"
                       inputmode="numeric" maxlength="6"
                       required>

                <label>Confirm New PIN</label>
                <input name="confirm_pin" type="password"
                       inputmode="numeric" maxlength="6"
                       required>

                <button type="submit">Update PIN</button>
            </form>

            <form action="/logout" method="post">
                {csrf_input(request)}
                <button type="submit">Log Out</button>
            </form>
        </div>
    """))


@app.post("/change-pin")
async def change_pin_route(
    request: Request,
    current_pin: str = Form(...),
    new_pin: str = Form(...),
    confirm_pin: str = Form(...)
):
    employee_id = request.session.get("employee_id")

    if not employee_id:
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))

    try:
        change_pin(
            employee_id,
            current_pin,
            new_pin,
            confirm_pin
        )
    except ValueError as error:
        return HTMLResponse(
            page(f"""
                <div class="card">
                    <h2>PIN Change Failed</h2>
                    <p class="error">{escape(str(error))}</p>
                    <a href="/change-pin">Try Again</a>
                </div>
            """),
            status_code=400
        )

    request.session.clear()
    return RedirectResponse("/", status_code=303)

# Ensure SessionMiddleware runs before PIN enforcement.
from starlette.middleware.sessions import SessionMiddleware as _SessionMiddleware
from starlette.middleware import Middleware

_session_layers = [
    m for m in app.user_middleware
    if m.cls is _SessionMiddleware
]

if _session_layers:
    session_layer = _session_layers[0]
    app.user_middleware.remove(session_layer)
    app.user_middleware.insert(0, session_layer)
    app.middleware_stack = None
    print("Session middleware order corrected!")
else:
    print("SessionMiddleware not found.")

# EMPLOYEE ATTENDANCE DASHBOARD

from fastapi import HTTPException
from attendance_service import clock_in, clock_out
from attendance_correction_service import (
    get_open_shift,
    get_employee_pending_correction,
    submit_clock_out_correction,
)

from attendance_correction_service import (
    pending_clock_out_corrections,
    review_clock_out_correction,
)

def attendance_employee(request):
    employee_id = request.session.get("employee_id")
    if not employee_id:
        raise HTTPException(401, "Login required")

    with get_connection() as conn:
        employee = conn.execute("""
            SELECT id, full_name, role, branch,
                   is_active, must_change_pin
            FROM employees WHERE id = ?
        """, (employee_id,)).fetchone()

    if not employee or not employee["is_active"]:
        raise HTTPException(403, "Account inactive")

    if employee["must_change_pin"]:
        raise HTTPException(403, "Change your PIN first")

    return dict(employee)


@app.get("/attendance", response_class=HTMLResponse)
def attendance_page(request: Request):
    employee = attendance_employee(request)

    # Forgotten Clock-Out Correction status
    open_shift = get_open_shift(employee["id"])
    pending_correction = (
        get_employee_pending_correction(employee["id"])
        if open_shift else None
    )

    with get_connection() as conn:
        records = conn.execute("""
            SELECT branch, clock_in, clock_out,
                   verification_status
            FROM attendance
            WHERE employee_id = ?
            ORDER BY id DESC LIMIT 10
        """, (employee["id"],)).fetchall()

    rows = "".join(
        "<tr>"
        f"<td>{escape(r['branch'])}</td>"
        f"<td>{escape(r['clock_in'])}</td>"
        f"<td>{escape(r['clock_out'] or 'Active')}</td>"
        f"<td>{escape(r['verification_status'])}</td>"
        "</tr>"
        for r in records
    )

    from attendance_service import effective_branch
    from datetime import datetime, timezone

    authorized = (
        effective_branch(
            employee["id"],
            datetime.now(timezone.utc).date().isoformat()
        )
        if employee["branch"] or employee["role"] != "GM"
        else None
    )


    correction_panel = ""

    if open_shift:
        if pending_correction:
            correction_panel = """
            <div class="attendance-panel">
                <h2>Forgotten Clock-Out</h2>
                <p><strong>Awaiting Manager Approval</strong></p>
                <p>Your correction request has been submitted.
                   Your unfinished shift will remain open until
                   an authorized manager or GM approves it.</p>
            </div>
            """
        else:
            correction_panel = f"""
            <div class="attendance-panel">
                <h2>Forgotten Clock-Out?</h2>
                <p>If you forgot to clock out, enter the actual
                   time you finished work and explain what happened.</p>
                <p>Your manager or GM must approve the request.
                   Your attendance will not change automatically.</p>
                <form id="forgotten-clockout-form"
                      action="/attendance/correction/request"
                      method="post">
                    {csrf_input(request)}
                    <label for="requested-clockout">
                        Actual Departure Date and Time
                    </label>
                    <input type="datetime-local"
                           id="requested-clockout"
                           name="requested_time"
                           required>
                    <label for="clockout-correction-reason">
                        Reason for Forgotten Clock-Out
                    </label>
                    <textarea id="clockout-correction-reason"
                              name="reason"
                              maxlength="500"
                              required
                              placeholder="Explain why you forgot to clock out"></textarea>
                    <button type="submit">
                        Submit Correction Request
                    </button>
                </form>
            </div>
            """

    gps_panel_start = ""
    gps_panel_end = ""

    if not authorized:
        gps_panel_start = """
        <div class="attendance-panel">
            <h2>GPS Attendance</h2>
            <p>Your GM account has access to all branches.</p>
            <p>GPS clock-in and clock-out require a specific
               authorized branch. Contact the system administrator
               to configure attendance access.</p>
        </div>
        """
        gps_panel_end = ""

    gps_controls = ""
    if authorized:
        gps_controls = f"""
        <div class="attendance-panel">
            <h2>Clock In / Clock Out</h2>
            <p>Enable your phone's location services before proceeding.</p>

            <div id="selfie-panel" style="
                border:1px solid #dce5ed;
                border-radius:12px;
                padding:16px;
                margin-bottom:18px;
                text-align:center;
            ">
                <h3>Attendance Selfie Verification</h3>
                <p>Take a live selfie before clocking in or out.
                   Your photo will be stored privately for
                   attendance verification.</p>

                <video id="selfie-video"
                       autoplay
                       playsinline
                       muted
                       style="width:100%;max-width:360px;
                              border-radius:12px;
                              display:none;"></video>

                <canvas id="selfie-canvas"
                        style="display:none;"></canvas>

                <img id="selfie-preview"
                     alt="Captured attendance selfie"
                     style="width:100%;max-width:360px;
                            border-radius:12px;
                            display:none;">

                <div style="margin-top:12px;">
                    <button type="button"
                            onclick="startSelfieCamera()">
                        Open Front Camera
                    </button>

                    <button type="button"
                            onclick="captureSelfie()">
                        Capture Selfie
                    </button>

                    <button type="button"
                            onclick="retakeSelfie()">
                        Retake Selfie
                    </button>
                </div>

                <p id="selfie-status" class="muted">
                    Camera not started.
                </p>
            </div>

            <div class="attendance-actions">
                <form id="clockin" action="/attendance/in" method="post">
                    {csrf_input(request)}
                    <label>Authorized Check-in Branch</label>
                    <p><strong>{escape(authorized or 'Not assigned')}</strong></p>
                    <input type="hidden"
                           name="branch"
                           value="{escape(authorized or '', quote=True)}">
                    <input type="hidden" name="latitude" id="lat">
                    <input type="hidden" name="longitude" id="lon">
                    <input type="hidden" name="accuracy" id="accuracy">
                    <input type="hidden"
                           name="selfie_data"
                           id="clockin-selfie">
                    <button type="button"
                            class="clockin-button"
                            onclick="checkIn()">
                        GPS Clock In
                    </button>
                </form>

                <form id="clockout" action="/attendance/out" method="post">
                    {csrf_input(request)}
                    <p>Finish your current shift.</p>
                    <input type="hidden" name="latitude" id="out-lat">
                    <input type="hidden" name="longitude" id="out-lon">
                    <input type="hidden" name="accuracy" id="out-accuracy">
                    <input type="hidden"
                           name="selfie_data"
                           id="clockout-selfie">
                    <button type="button"
                            class="clockout-button"
                            onclick="checkOut()">
                        GPS Clock Out
                    </button>
                </form>
            </div>

            <button type="button"
                    onclick="testGPSLocation()">
                Test GPS Location
            </button>
            <p id="gps-status" class="muted"></p>
        </div>

        <div class="attendance-panel">
            <h2>GPS Location Problem?</h2>
            <p>If your GPS location is inaccurate, request
               manager verification.</p>

            <form id="gps-exception"
                  action="/attendance/gps-exception"
                  method="post">
                {csrf_input(request)}

                <label>Reason for GPS Exception</label>
                <textarea name="reason"
                          id="exception-reason"
                          maxlength="500"
                          required
                          placeholder="Explain the GPS problem"></textarea>

                <input type="hidden" name="branch"
                       id="exception-branch">
                <input type="hidden" name="latitude"
                       id="exception-lat">
                <input type="hidden" name="longitude"
                       id="exception-lon">
                <input type="hidden" name="accuracy"
                       id="exception-accuracy">

                <button type="button"
                        class="exception-button"
                        onclick="requestGPSException()">
                    Request GPS Exception
                </button>
            </form>
        </div>

        """

    return HTMLResponse(page(f"""
    <style>
        .attendance-wrap {{
            max-width: 900px;
            margin: 0 auto;
            font-family: Arial, sans-serif;
        }}
        .attendance-header {{
            background: #123b56;
            color: white;
            padding: 26px;
            border-radius: 16px;
            margin-bottom: 18px;
        }}
        .attendance-header h1 {{
            color: white;
            margin: 8px 0;
        }}
        .attendance-header p {{
            color: #e0edf5;
        }}
        .attendance-panel {{
            background: white;
            border: 1px solid #dce5ed;
            border-radius: 14px;
            padding: 20px;
            margin-bottom: 16px;
        }}
        .attendance-panel h2 {{
            margin-top: 0;
            color: #123b56;
        }}
        .attendance-actions {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 16px;
        }}
        .attendance-actions button {{
            width: 100%;
            padding: 16px;
            border: none;
            border-radius: 10px;
            color: white;
            font-weight: bold;
            cursor: pointer;
        }}
        .clockin-button {{
            background: #16764a;
        }}
        .clockout-button {{
            background: #123b56;
        }}
        .exception-button {{
            background: #a65d12;
            color: white;
            padding: 14px;
            border: none;
            border-radius: 10px;
            cursor: pointer;
        }}
        .attendance-wrap select,
        .attendance-wrap textarea {{
            width: 100%;
            box-sizing: border-box;
            padding: 12px;
            border: 1px solid #cbd5df;
            border-radius: 8px;
            margin: 8px 0 14px;
        }}
        .attendance-table {{
            width: 100%;
            border-collapse: collapse;
        }}
        .attendance-table th,
        .attendance-table td {{
            padding: 12px;
            border-bottom: 1px solid #e2e8ef;
            text-align: left;
        }}
        .attendance-table th {{
            background: #edf3f8;
        }}
        .attendance-table-wrap {{
            overflow-x: auto;
        }}
        .attendance-back {{
            display: inline-block;
            margin: 12px 0 24px;
        }}
        @media (max-width: 600px) {{
            .attendance-actions {{
                grid-template-columns: 1fr;
            }}
            .attendance-header {{
                padding: 20px;
            }}
        }}

        .attendance-actions {{
            display: grid !important;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            grid-template-areas:
                "branch branch"
                "clockin clockout";
            gap: 12px;
            align-items: stretch;
        }}
        .attendance-actions #clockin {{
            display: contents;
        }}
        .attendance-actions #clockin label {{
            grid-area: branch;
            margin-bottom: -8px;
        }}
        .attendance-actions #clockin select {{
            grid-column: 1 / -1;
            grid-row: 1;
            margin-top: 24px;
        }}
        .attendance-actions #clockin button {{
            grid-area: clockin;
        }}
        .attendance-actions #clockout {{
            grid-area: clockout;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
        }}
        .attendance-actions #clockout p {{
            display: none;
        }}
        .attendance-actions button {{
            min-height: 54px;
            width: 100%;
        }}

        /* Keep the branch label and dropdown on separate rows */
        .attendance-actions {{
            grid-template-rows: auto auto auto;
            grid-template-areas:
                "branch branch"
                "selector selector"
                "clockin clockout";
            align-items: stretch;
        }}
        .attendance-actions #clockin label {{
            grid-area: branch;
            margin: 0;
            align-self: end;
        }}
        .attendance-actions #clockin select {{
            grid-area: selector;
            grid-column: 1 / -1;
            grid-row: auto;
            margin: 0 0 8px;
        }}
        .attendance-actions #clockin button {{
            grid-area: clockin;
        }}
        .attendance-actions #clockout {{
            grid-area: clockout;
            justify-content: flex-end;
        }}
        .attendance-actions #clockout button {{
            margin: 0;
        }}

        /* MRA attendance button colours */
        .attendance-wrap .clockin-button {{
            background-color: #16764A !important;
            color: white !important;
        }}
        .attendance-wrap .clockout-button {{
            background-color: #2563EB !important;
            color: white !important;
        }}
        .attendance-wrap .exception-button {{
            background-color: #DC2626 !important;
            color: white !important;
        }}
    </style>

    <div class="attendance-wrap">
        <div class="attendance-header">
            <small>MRA TRADING COMPANY LIMITED</small>
            <h1>GPS Attendance</h1>
            <p>Welcome, {escape(employee['full_name'])}</p>
            <p><strong>Assigned Branch:</strong>
                {escape(employee['branch'] or 'All Branches')}</p>
        </div>

        {gps_panel_start}
        {"<!-- GPS_CONTROLS_START -->" if authorized else ""}
        {gps_controls}
        {"<!-- GPS_CONTROLS_END -->" if authorized else ""}
        {gps_panel_end}
        {correction_panel}
        <div class="attendance-panel">
            <h2>Recent Attendance History</h2>
            <div class="attendance-table-wrap">
                <table class="attendance-table">
                    <thead>
                        <tr>
                            <th>Branch</th>
                            <th>Clock In</th>
                            <th>Clock Out</th>
                            <th>Verification</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
        </div>

        <a class="attendance-back" href="/dashboard">
            Back to Dashboard
        </a>
    </div>

    <script>

    let selfieStream = null;
    let capturedSelfie = "";

    function stopSelfieCamera() {{
        if (selfieStream) {{
            selfieStream.getTracks().forEach(track => track.stop());
            selfieStream = null;
        }}

        const video = document.getElementById("selfie-video");
        video.pause();
        video.srcObject = null;
        video.style.display = "none";
    }}

    async function startSelfieCamera() {{
        const status = document.getElementById("selfie-status");
        const video = document.getElementById("selfie-video");
        const preview = document.getElementById("selfie-preview");

        if (!window.isSecureContext || !navigator.mediaDevices ||
            !navigator.mediaDevices.getUserMedia) {{
            status.textContent =
                "Camera unavailable. Use HTTPS or localhost and allow camera access.";
            return;
        }}

        stopSelfieCamera();
        capturedSelfie = "";
        document.getElementById("clockin-selfie").value = "";
        document.getElementById("clockout-selfie").value = "";
        preview.style.display = "none";
        preview.removeAttribute("src");

        status.textContent = "Requesting front camera permission...";

        try {{
            try {{
                selfieStream = await navigator.mediaDevices.getUserMedia({{
                    audio: false,
                    video: {{
                        facingMode: "user",
                        width: {{ ideal: 640 }},
                        height: {{ ideal: 480 }}
                    }}
                }});
            }} catch (preferredCameraError) {{
                selfieStream = await navigator.mediaDevices.getUserMedia({{
                    audio: false,
                    video: true
                }});
            }}

            video.srcObject = selfieStream;
            video.style.display = "block";
            await video.play();

            status.textContent =
                "Camera ready. Position your face and select Capture Selfie.";
        }} catch (error) {{
            stopSelfieCamera();
            status.textContent = "Camera error: " + error.message;
        }}
    }}

    function captureSelfie() {{
        const status = document.getElementById("selfie-status");
        const video = document.getElementById("selfie-video");
        const canvas = document.getElementById("selfie-canvas");
        const preview = document.getElementById("selfie-preview");

        if (!selfieStream || !video.videoWidth || !video.videoHeight) {{
            status.textContent = "Open the camera before capturing a selfie.";
            return;
        }}

        const maxDimension = 960;
        const scale = Math.min(
            1,
            maxDimension / Math.max(video.videoWidth, video.videoHeight)
        );

        canvas.width = Math.round(video.videoWidth * scale);
        canvas.height = Math.round(video.videoHeight * scale);

        const context = canvas.getContext("2d");
        if (!context) {{
            status.textContent = "Unable to capture photo.";
            return;
        }}

        context.drawImage(video, 0, 0, canvas.width, canvas.height);

        capturedSelfie = canvas.toDataURL("image/jpeg", 0.82);

        document.getElementById("clockin-selfie").value = capturedSelfie;
        document.getElementById("clockout-selfie").value = capturedSelfie;

        preview.src = capturedSelfie;
        preview.style.display = "block";

        stopSelfieCamera();

        status.textContent =
            "Selfie captured. Review your photo before clocking in or out.";
    }}

    function retakeSelfie() {{
        capturedSelfie = "";

        document.getElementById("clockin-selfie").value = "";
        document.getElementById("clockout-selfie").value = "";

        const preview = document.getElementById("selfie-preview");
        preview.style.display = "none";
        preview.removeAttribute("src");

        startSelfieCamera();
    }}

    window.addEventListener("pagehide", stopSelfieCamera);

    function testGPSLocation() {{
        const status = document.getElementById("gps-status");
        status.textContent = "Testing GPS location...";

        if (!window.isSecureContext) {{
            status.textContent =
                "GPS test failed: Browser does not trust this secure context.";
            return;
        }}

        if (!navigator.geolocation) {{
            status.textContent =
                "GPS test failed: Geolocation API unavailable.";
            return;
        }}

        navigator.geolocation.getCurrentPosition(
            function(position) {{
                status.textContent =
                    "GPS test successful. Accuracy: " +
                    Math.round(position.coords.accuracy) +
                    " metres. No attendance submitted.";
            }},
            function(error) {{
                status.textContent =
                    "GPS test failed: " + error.message;
            }},
            {{
                enableHighAccuracy: true,
                timeout: 20000,
                maximumAge: 0
            }}
        );
    }}

    function setAttendanceLoading(formId, loading, message) {{
        const form = document.getElementById(formId);
        const button = form.querySelector('button');
        if (!button.dataset.originalText) {{
            button.dataset.originalText = button.textContent.trim();
        }}
        button.disabled = loading;
        button.setAttribute("aria-busy", String(loading));
        button.textContent = loading
            ? message
            : button.dataset.originalText;
    }}

    function attendanceIsLoading(formId) {{
        return document.getElementById(formId)
            .querySelector('button').disabled;
    }}

    function checkIn() {{
        const status = document.getElementById("gps-status");

        if (attendanceIsLoading("clockin")) {{
            return;
        }}

        if (!capturedSelfie ||
            !document.getElementById("clockin-selfie").value) {{
            status.textContent =
                "Please open the camera and capture your selfie before clocking in.";
            return;
        }}
        setAttendanceLoading(
            "clockin", true, "Checking GPS location..."
        );
        status.textContent = "Checking your location...";

        if (!navigator.geolocation) {{
            status.textContent = "GPS not supported.";
            setAttendanceLoading("clockin", false, "");
            return;
        }}

        navigator.geolocation.getCurrentPosition(
            function(position) {{
                document.getElementById("lat").value =
                    position.coords.latitude;
                document.getElementById("lon").value =
                    position.coords.longitude;
                document.getElementById("accuracy").value =
                    position.coords.accuracy;
                const c = position.coords;
                status.textContent =
                    "Latitude: " + c.latitude +
                    " | Longitude: " + c.longitude +
                    " | Accuracy: " + c.accuracy + " metres";
                
                setAttendanceLoading(
                    "clockin", true, "Submitting attendance... Please wait"
                );
                document.getElementById("clockin").submit();
            }},
            function(error) {{
                status.textContent =
                    "Location unavailable: " + error.message;
                setAttendanceLoading("clockin", false, "");
            }},
            {{
                enableHighAccuracy: true,
                timeout: 20000,
                maximumAge: 0
            }}
        );
    }}
    

    function requestGPSException() {{
        const status = document.getElementById("gps-status");
        const form = document.getElementById("gps-exception");

        if (attendanceIsLoading("gps-exception")) {{
            return;
        }}
        const reason = document.getElementById("exception-reason").value.trim();

        if (!reason) {{
            status.textContent = "Please explain the GPS problem.";
            return;
        }}

        if (!navigator.geolocation) {{
            status.textContent = "Location services are not supported.";
            return;
        }}

        const branch = document.querySelector(
            '#clockin input[name="branch"]'
        ).value;

        setAttendanceLoading(
            "gps-exception", true, "Checking GPS location..."
        );
        status.textContent = "Collecting GPS for exception request...";

        navigator.geolocation.getCurrentPosition(
            function(position) {{
                document.getElementById("exception-branch").value = branch;
                document.getElementById("exception-lat").value =
                    position.coords.latitude;
                document.getElementById("exception-lon").value =
                    position.coords.longitude;
                document.getElementById("exception-accuracy").value =
                    position.coords.accuracy;

                status.textContent = "Submitting exception request...";
                setAttendanceLoading(
                    "gps-exception", true, "Submitting request... Please wait"
                );
                form.submit();
            }},
            function(error) {{
                status.textContent =
                    "Unable to collect GPS: " + error.message;
                setAttendanceLoading("gps-exception", false, "");
            }},
            {{
                enableHighAccuracy: true,
                timeout: 20000,
                maximumAge: 0
            }}
        );
    }}

    function checkOut() {{

        const status = document.getElementById("gps-status");

        if (attendanceIsLoading("clockout")) {{
            return;
        }}

        if (!capturedSelfie ||
            !document.getElementById("clockout-selfie").value) {{
            status.textContent =
                "Please open the camera and capture your selfie before clocking out.";
            return;
        }}
        setAttendanceLoading(
            "clockout", true, "Checking GPS location..."
        );
        status.textContent = "Checking GPS for clock-out...";

        if (!navigator.geolocation) {{
            status.textContent = "GPS not supported.";
            setAttendanceLoading("clockout", false, "");
            return;
        }}

        navigator.geolocation.getCurrentPosition(
            function(position) {{
                document.getElementById("out-lat").value =
                    position.coords.latitude;

                document.getElementById("out-lon").value =
                    position.coords.longitude;

                document.getElementById("out-accuracy").value =
                    position.coords.accuracy;

                setAttendanceLoading(
                    "clockout", true, "Submitting attendance... Please wait"
                );
                document.getElementById("clockout").submit();
            }},
            function(error) {{
                status.textContent =
                    "Location unavailable: " + error.message;
                setAttendanceLoading("clockout", false, "");
            }},
            {{
                enableHighAccuracy: true,
                timeout: 20000,
                maximumAge: 0
            }}
        );
    }}

    </script>
    """))


@app.post("/attendance/in")
def attendance_in(
    request: Request,
    branch: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    accuracy: float = Form(...),
    selfie_data: str = Form(""),
    csrf_token: str = Form("")
):
    validate_csrf(request, csrf_token)
    employee = attendance_employee(request)

    try:
        distance = clock_in(
            employee["id"], branch,
            latitude, longitude, accuracy,
            selfie_data
        )
        message = (
            f"Clock-in recorded. GPS distance: {distance} metres. "
            "Awaiting manager verification."
        )
    except ValueError as error:
        message = str(error)
    except UniqueViolation as error:
        if error.diag.constraint_name != "attendance_one_open_shift_per_employee":
            raise
        message = (
            "You are already clocked in. "
            "Please clock out before starting another shift."
        )

    return HTMLResponse(page(f"""
        <div class="card">
            <h2>Attendance Result</h2>
            <p>{escape(message)}</p>
            <a href="/attendance">Back to Attendance</a>
        </div>
    """))



@app.post("/attendance/correction/request")
def attendance_correction_request(
    request: Request,
    requested_time: str = Form(...),
    reason: str = Form(...),
    csrf_token: str = Form("")
):
    validate_csrf(request, csrf_token)
    employee = attendance_employee(request)

    try:
        request_id = submit_clock_out_correction(
            employee["id"],
            requested_time,
            reason
        )
        message = (
            f"Clock-Out Correction Request #{request_id} submitted. "
            "Status: PENDING. Your manager or GM must review "
            "and approve the request before your unfinished "
            "attendance shift can be closed."
        )
    except ValueError as error:
        message = str(error)

    return HTMLResponse(page(f"""
        <div class="card">
            <h2>Clock-Out Correction Request Result</h2>
            <p>{escape(message)}</p>
            <a href="/attendance">Back to Attendance</a>
        </div>
    """))


@app.post("/attendance/gps-exception")
def attendance_gps_exception(
    request: Request,
    branch: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    accuracy: float = Form(...),
    reason: str = Form(...),
    csrf_token: str = Form("")
):
    validate_csrf(request, csrf_token)
    employee = attendance_employee(request)

    try:
        request_id = request_gps_exception(
            employee["id"],
            branch,
            latitude,
            longitude,
            accuracy,
            reason
        )
        message = (
            f"GPS Exception Request #{request_id} submitted. "
            "Status: PENDING. Your manager must physically "
            "confirm your presence before attendance is recorded."
        )
    except ValueError as error:
        message = str(error)

    return HTMLResponse(page(f"""
        <div class="card">
            <h2>GPS Exception Request Result</h2>
            <p>{escape(message)}</p>
            <a href="/attendance">Back to Attendance</a>
        </div>
    """))


@app.post("/attendance/out")
def attendance_out(
    request: Request,
    latitude: float = Form(...),
    longitude: float = Form(...),
    accuracy: float = Form(...),
    selfie_data: str = Form(""),
    csrf_token: str = Form("")
):
    validate_csrf(request, csrf_token)
    employee = attendance_employee(request)

    try:
        clock_out(
            employee["id"],
            latitude,
            longitude,
            accuracy,
            selfie_data
        )
        message = "Clock-out recorded successfully."
    except ValueError as error:
        message = str(error)

    return HTMLResponse(page(f"""
        <div class="card">
            <h2>Attendance Result</h2>
            <p>{escape(message)}</p>
            <a href="/attendance">Back to Attendance</a>
        </div>
    """))

# MANAGER ATTENDANCE VERIFICATION DASHBOARD

from verification_service import (
    pending_attendance,
    verify_attendance
)

def require_verification_manager(request):
    employee = attendance_employee(request)

    if employee["role"] not in ("GM", "MANAGER"):
        raise HTTPException(
            status_code=403,
            detail="Manager access required"
        )

    return employee


@app.get("/gm/dashboard", response_class=HTMLResponse)
def gm_dashboard(request: Request):
    gm = attendance_employee(request)

    if gm["role"] != "GM":
        raise HTTPException(
            status_code=403,
            detail="General Manager access required"
        )

    with get_connection() as conn:
        totals = conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM employees
                 WHERE is_active = 1) AS employees,
                (SELECT COUNT(*) FROM attendance
                 WHERE clock_out IS NULL) AS clocked_in,
                (SELECT COUNT(*) FROM attendance
                 WHERE verification_status = 'PENDING') AS pending,
                (SELECT COUNT(*) FROM gps_exception_requests
                 WHERE status = 'PENDING') AS exceptions
        """).fetchone()

        branch_rows = conn.execute("""
            SELECT
                b.name,
                (SELECT COUNT(*) FROM employees e
                 WHERE e.branch = b.name
                   AND e.is_active = 1) AS employees,
                (SELECT COUNT(*) FROM attendance a
                 WHERE a.branch = b.name
                   AND a.clock_out IS NULL) AS clocked_in,
                (SELECT COUNT(*) FROM attendance a
                 WHERE a.branch = b.name
                   AND a.verification_status = 'PENDING') AS pending
            FROM branches b
            WHERE b.is_active = 1
            ORDER BY b.name
        """).fetchall()

    branch_cards = ""

    for branch in branch_rows:
        branch_cards += f"""
            <div class="gm-branch">
                <h3>{escape(branch['name'])}</h3>
                <div class="gm-branch-numbers">
                    <span>Employees: <b>{branch['employees']}</b></span>
                    <span>Clocked In: <b>{branch['clocked_in']}</b></span>
                    <span>Pending: <b>{branch['pending']}</b></span>
                </div>
            </div>
        """

    if not branch_cards:
        branch_cards = "<p>No active branches configured.</p>"

    return HTMLResponse(page(f"""
        <style>
            .gm-wrap {{
                max-width: 1050px;
                margin: 0 auto;
                font-family: Arial, sans-serif;
            }}
            .gm-header {{
                background: #123b56;
                color: white;
                border-radius: 16px;
                padding: 28px;
                margin-bottom: 20px;
            }}
            .gm-header h1 {{
                color: white;
                margin: 8px 0;
            }}
            .gm-header p {{
                color: #dceaf2;
            }}
            .gm-stats {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 16px;
                margin-bottom: 25px;
            }}
            .gm-stat {{
                background: white;
                border: 1px solid #dce5ed;
                border-radius: 12px;
                padding: 22px;
            }}
            .gm-stat small {{
                display: block;
                color: #627487;
                margin-bottom: 12px;
            }}
            .gm-stat strong {{
                font-size: 30px;
                color: #123b56;
            }}
            .gm-branch {{
                background: #edf3f8;
                border-radius: 12px;
                padding: 20px;
                margin-bottom: 12px;
            }}
            .gm-branch h3 {{
                margin-top: 0;
                color: #123b56;
            }}
            .gm-branch-numbers {{
                display: flex;
                flex-wrap: wrap;
                gap: 20px;
            }}
            .gm-links a {{
                display: block;
                padding: 15px;
                margin: 10px 0;
                background: #edf3f8;
                border-radius: 10px;
                color: #123b56;
                text-decoration: none;
                font-weight: bold;
            }}
            @media (max-width: 600px) {{
                .gm-stats {{
                    grid-template-columns: 1fr;
                }}
            }}

            .gm-brand {{
                display: flex;
                align-items: center;
                gap: 18px;
                margin-bottom: 15px;
            }}
            .gm-logo {{
                width: 86px;
                height: 86px;
                object-fit: contain;
                background: white;
                border-radius: 50%;
                padding: 5px;
                flex-shrink: 0;
            }}
            .gm-header {{
                border-bottom: 5px solid #EF4655;
            }}
            @media (max-width: 600px) {{
                .gm-logo {{
                    width: 65px;
                    height: 65px;
                }}
                .gm-brand {{
                    gap: 12px;
                }}
            }}
        
            .gm-header {{
                position: relative;
            }}
            .gm-logout {{
                position: absolute;
                top: 18px;
                right: 20px;
                margin: 0;
                z-index: 2;
            }}
            .gm-logout button {{
                background: #EF4655;
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 10px 18px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
            }}
            .gm-logout button:hover {{
                background: #D93647;
            }}
            @media (max-width: 600px) {{
                .gm-logout {{
                    position: static;
                    margin-bottom: 15px;
                    text-align: right;
                }}
            }}
</style>

        <div class="gm-wrap">
            <div class="gm-header">
                <form class="gm-logout"
                      action="/logout"
                      method="post">
                    {csrf_input(request)}
                    <button type="submit">Log Out</button>
                </form>

                <div class="gm-brand">
                    <img
                        src="/static/images/mra-logo.jpeg"
                        alt="MRA Trading Company Limited Logo"
                        class="gm-logo"
                    >
                    <div>
                        <small>MRA TRADING COMPANY LIMITED</small>
                        <h1>General Manager Dashboard</h1>
                    </div>
                </div>
                <h2>Welcome, {escape(gm['full_name'])}</h2>
                <p>Company-wide attendance overview</p>
            </div>

            <div class="gm-stats">
                <div class="gm-stat">
                    <small>Active Employees</small>
                    <strong>{totals['employees']}</strong>
                </div>
                <div class="gm-stat">
                    <small>Currently Clocked In</small>
                    <strong>{totals['clocked_in']}</strong>
                </div>
                <div class="gm-stat">
                    <small>Pending Verification</small>
                    <strong>{totals['pending']}</strong>
                </div>
                <div class="gm-stat">
                    <small>Pending GPS Exceptions</small>
                    <strong>{totals['exceptions']}</strong>
                </div>
            </div>

            <h2>Branch Attendance Overview</h2>
            {branch_cards}

            <h2>GM Quick Actions</h2>
            <div class="gm-links">
                <a href="/employees">Employee Management</a>
                <a href="/branches">Branch Management</a>
                <a href="/transfers">Branch Transfers</a>
                <a href="/reports">Attendance Reports</a>
                <a href="/manager/attendance">Verify Attendance</a>
                <a href="/manager/gps-exceptions">GPS Exceptions</a>
                <a href="/manager/clockout-corrections">Clock-Out Corrections</a>
                <a href="/manager/dashboard">Manager Dashboard</a>
                <a href="/attendance">My GPS Attendance</a>
                <a href="/dashboard">Main Dashboard</a>
            </div>
        </div>
    """))


@app.get("/manager/dashboard", response_class=HTMLResponse)
def manager_dashboard(request: Request):
    manager = require_verification_manager(request)

    role = manager["role"]
    branch = manager["branch"]

    if role == "GM":
        scope = ""
        params = ()
        branch_label = "All Branches"
    else:
        if not branch:
            raise HTTPException(403, "Manager branch not assigned")
        scope = " WHERE branch = ?"
        params = (branch,)
        branch_label = branch

    with get_connection() as conn:
        employees_scope = (
            " WHERE is_active = 1"
            + (" AND branch = ?" if role != "GM" else "")
        )
        employees_params = params

        total_employees = conn.execute(
            "SELECT COUNT(*) FROM employees" + employees_scope,
            employees_params
        ).fetchone()[0]

        active_shifts = conn.execute(
            "SELECT COUNT(*) FROM attendance"
            + (" WHERE branch = ? AND clock_out IS NULL"
               if role != "GM"
               else " WHERE clock_out IS NULL"),
            params
        ).fetchone()[0]

        pending_verifications = conn.execute(
            "SELECT COUNT(*) FROM attendance"
            + (" WHERE branch = ? AND verification_status = 'PENDING'"
               if role != "GM"
               else " WHERE verification_status = 'PENDING'"),
            params
        ).fetchone()[0]

        pending_exceptions = conn.execute(
            "SELECT COUNT(*) FROM gps_exception_requests"
            + (" WHERE branch = ? AND status = 'PENDING'"
               if role != "GM"
               else " WHERE status = 'PENDING'"),
            params
        ).fetchone()[0]

    safe_name = escape(manager["full_name"])
    safe_branch = escape(branch_label)

    return HTMLResponse(page(f"""
        <style>
            .manager-dashboard {{
                max-width: 950px;
                margin: 0 auto;
                font-family: Arial, sans-serif;
            }}
            .manager-header {{
                background: #123b56;
                color: white;
                padding: 28px;
                border-radius: 16px;
                margin-bottom: 20px;
            }}
            .manager-header h1 {{
                color: white;
                margin: 8px 0;
            }}
            .manager-header p {{
                color: #dceaf2;
            }}
            .manager-grid {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 16px;
                margin-bottom: 20px;
            }}
            .manager-stat {{
                background: white;
                border: 1px solid #dce5ed;
                border-radius: 12px;
                padding: 22px;
            }}
            .manager-stat small {{
                color: #627487;
                display: block;
                margin-bottom: 12px;
            }}
            .manager-stat strong {{
                font-size: 30px;
                color: #123b56;
            }}
            .manager-links a {{
                display: block;
                padding: 17px;
                margin: 12px 0;
                background: #edf3f8;
                border-radius: 10px;
                color: #123b56;
                text-decoration: none;
                font-weight: bold;
            }}
            @media (max-width: 600px) {{
                .manager-grid {{
                    grid-template-columns: 1fr;
                }}
            }}
        
            .manager-header {{
                background: #123B56;
                color: #FFFFFF;
                border-bottom: 5px solid #EF4655;
                position: relative;
                padding-left: 125px;
                min-height: 105px;
            }}
            .manager-header h1 {{
                color: #FFFFFF;
            }}
            .manager-header p {{
                color: #E5EDF4;
            }}
            .manager-logo {{
                position: absolute;
                left: 20px;
                top: 50%;
                transform: translateY(-50%);
                width: 86px;
                height: 86px;
                object-fit: contain;
                background: white;
                border-radius: 50%;
                padding: 5px;
                box-sizing: border-box;
            }}
            @media (max-width: 600px) {{
                .manager-header {{
                    padding-left: 90px;
                }}
                .manager-logo {{
                    width: 62px;
                    height: 62px;
                    left: 14px;
                }}
            }}

            .manager-header {{
                position: relative;
            }}
            .manager-logout {{
                position: absolute;
                top: 18px;
                right: 20px;
                margin: 0;
                z-index: 2;
            }}
            .manager-logout button {{
                background: #EF4655;
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 10px 18px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
            }}
            .manager-logout button:hover {{
                background: #D93647;
            }}
            @media (max-width: 600px) {{
                .manager-logout {{
                    position: static;
                    margin-bottom: 15px;
                    text-align: right;
                }}
            }}
</style>

        <div class="manager-dashboard">
            <div class="manager-header">
                <form class="manager-logout"
                      action="/logout"
                      method="post">
                    {csrf_input(request)}
                    <button type="submit">Log Out</button>
                </form>

                <img
                    src="/static/images/mra-logo.jpeg"
                    alt="MRA Trading Company Limited Logo"
                    class="manager-logo"
                >
                <small>MRA TRADING COMPANY LIMITED</small>
                <h1>Manager Dashboard</h1>
                <h2>Welcome, {safe_name}</h2>
                <p>Branch: {safe_branch}</p>
            </div>

            <div class="manager-grid">
                <div class="manager-stat">
                    <small>Active Employees</small>
                    <strong>{total_employees}</strong>
                </div>
                <div class="manager-stat">
                    <small>Currently Clocked In</small>
                    <strong>{active_shifts}</strong>
                </div>
                <div class="manager-stat">
                    <small>Pending Verification</small>
                    <strong>{pending_verifications}</strong>
                </div>
                <div class="manager-stat">
                    <small>Pending GPS Exceptions</small>
                    <strong>{pending_exceptions}</strong>
                </div>
            </div>

            <h2>Manager Actions</h2>
            <div class="manager-links">
                <a href="/manager/attendance">
                    Verify Staff Attendance
                </a>
                <a href="/manager/gps-exceptions">
                    Review GPS Exception Requests
                </a>
                <a href="/attendance">
                    My GPS Attendance
                </a>
                <a href="/dashboard">
                    Back to Main Dashboard
                </a>
            </div>
        </div>
    """))



@app.get("/manager/attendance/{attendance_id}/photo/{action}")
def manager_attendance_photo(
    request: Request,
    attendance_id: int,
    action: str
):
    manager = require_verification_manager(request)

    try:
        photo_path = authorized_attendance_photo(
            manager["id"],
            attendance_id,
            action
        )

    except (PermissionError, FileNotFoundError, ValueError):
        raise HTTPException(
            status_code=404,
            detail="Attendance photograph unavailable."
        )

    return Response(
        content=photo_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline"
        }
    )



@app.get("/manager/clockout-corrections", response_class=HTMLResponse)
def manager_clockout_corrections_page(request: Request):
    employee = attendance_employee(request)

    try:
        requests = pending_clock_out_corrections(employee["id"])
    except ValueError as error:
        raise HTTPException(status_code=403, detail=str(error))

    cards = []

    for record in requests:
        request_id = int(record["id"])

        cards.append(f"""
        <div class="card">
            <h3>Correction Request #{request_id}</h3>
            <p><strong>Employee:</strong>
               {escape(str(record["full_name"]))}</p>
            <p><strong>Branch:</strong>
               {escape(str(record["branch"]))}</p>
            <p><strong>Original Clock-In:</strong>
               {escape(str(record["original_clock_in"]))}</p>
            <p><strong>Requested Clock-Out:</strong>
               {escape(str(record["requested_clock_out"]))}</p>
            <p><strong>Reason:</strong>
               {escape(str(record["reason"]))}</p>
            <p><strong>Submitted:</strong>
               {escape(str(record["requested_at"]))}</p>

            <form action="/manager/clockout-corrections/review"
                  method="post">
                {csrf_input(request)}
                <input type="hidden"
                       name="correction_id"
                       value="{request_id}">

                <label>Review Remarks</label>
                <textarea name="remarks"
                          maxlength="500"
                          placeholder="Optional review notes"></textarea>

                <button type="submit"
                        name="decision"
                        value="APPROVED">
                    Approve Correction
                </button>

                <button type="submit"
                        name="decision"
                        value="REJECTED">
                    Reject Correction
                </button>
            </form>
        </div>
        """)

    content = "".join(cards) if cards else (
        "<div class='card'>"
        "<p>No pending clock-out correction requests.</p>"
        "</div>"
    )

    return HTMLResponse(page(f"""
        <div style="max-width:900px;margin:auto;">
            <h1>Clock-Out Correction Requests</h1>
            <p>Review employee requests to correct forgotten clock-outs.</p>
            {content}
            <p><a href="/dashboard">Back to Dashboard</a></p>
        </div>
    """))



@app.post("/manager/clockout-corrections/review")
def manager_clockout_correction_review(
    request: Request,
    correction_id: int = Form(...),
    decision: str = Form(...),
    remarks: str = Form(""),
    csrf_token: str = Form("")
):
    validate_csrf(request, csrf_token)
    employee = attendance_employee(request)

    if employee["role"] not in ("MANAGER", "GM"):
        raise HTTPException(status_code=403, detail="Manager or GM access required.")

    try:
        review_clock_out_correction(
            employee["id"],
            correction_id,
            decision,
            remarks
        )
        message = (
            f"Correction Request #{correction_id} "
            f"successfully {decision.lower()}."
        )
    except ValueError as error:
        message = str(error)

    return HTMLResponse(page(f"""
        <div class="card">
            <h2>Clock-Out Correction Review Result</h2>
            <p>{escape(message)}</p>
            <a href="/manager/clockout-corrections">
                Back to Correction Requests
            </a>
        </div>
    """))


@app.get("/manager/attendance", response_class=HTMLResponse)
def manager_attendance_page(request: Request):
    manager = require_verification_manager(request)

    try:
        records = pending_attendance(manager["id"])
    except ValueError as error:
        raise HTTPException(403, str(error))

    rows = ""

    for record in records:
        record_id = int(record["id"])

        def selfie_preview(action, available, label):
            if not available:
                return "<span>Not available</span>"

            url = f"/manager/attendance/{record_id}/photo/{action}"

            return (
                f'<a href="{url}" target="_blank" '
                f'rel="noopener noreferrer">'
                f'<img src="{url}" alt="{label}" loading="lazy" '
                f'style="width:90px;height:90px;object-fit:cover;'
                f'border-radius:8px;"></a>'
            )

        clock_in_preview = selfie_preview(
            "in", record["clock_in_photo"], "Clock-in selfie"
        )

        clock_out_preview = selfie_preview(
            "out", record["clock_out_photo"], "Clock-out selfie"
        )

        rows += f"""
        <tr>
            <td>{escape(record['full_name'])}</td>
            <td>{escape(record['branch'])}</td>
            <td>{escape(record['clock_in'])}</td>
            <td>{record['gps_accuracy'] if record['gps_accuracy'] is not None else 'Not recorded'} m</td>
            <td>{record['gps_distance'] if record['gps_distance'] is not None else 'Not recorded'} m</td>
            <td>{escape(record['gps_verification_level'] or 'UNCLASSIFIED')} — {'Physical confirmation required' if record['gps_verification_level'] != 'STANDARD' else 'Standard GPS review'}</td>
            <td>
                <div style="display:flex;gap:12px;flex-wrap:wrap;">
                    <div>
                        <strong>Clock In</strong>
                        <div>{clock_in_preview}</div>
                    </div>
                    <div>
                        <strong>Clock Out</strong>
                        <div>{clock_out_preview}</div>
                    </div>
                </div>
            </td>
            <td>
                <form action="/manager/verify"
                      method="post">
                    {csrf_input(request)}
                    <input type="hidden"
                           name="attendance_id"
                           value="{record_id}">
                    <label style="display:block;margin-bottom:8px">
                        <input type="checkbox"
                               name="physical_confirmed"
                               value="yes">
                        I have physically confirmed this employee
                        is present at the branch.
                    </label>
                    <button name="decision"
                            value="APPROVED"
                            type="submit">
                        Approve
                    </button>
                    <button name="decision"
                            value="REJECTED"
                            type="submit"
                            style="background:#b42318">
                        Reject
                    </button>
                </form>
            </td>
        </tr>
        """

    if not rows:
        rows = """
        <tr>
            <td colspan="8">
                No attendance records awaiting verification.
            </td>
        </tr>
        """

    return HTMLResponse(page(f"""
        <div class="card" style="max-width:1000px">
            <h1>Manager Attendance Verification</h1>

            <p>Welcome, {escape(manager['full_name'])}</p>
            <p>Role: {escape(manager['role'])}</p>

            <h3>Pending Attendance</h3>

            <div style="overflow-x:auto">
                <table border="1" cellpadding="10"
                       style="border-collapse:collapse;width:100%">
                    <tr>
                        <th>Employee</th>
                        <th>Branch</th>
                        <th>Clock In</th>
                        <th>GPS Accuracy</th>
                        <th>GPS Distance</th>
                        <th>GPS Review</th>
                        <th>Selfie Verification</th>
                        <th>Action</th>
                    </tr>
                    {rows}
                </table>
            </div>

            <p><a href="/dashboard">Back to Dashboard</a></p>
        </div>
    """))


@app.post("/manager/verify")
async def manager_verify_route(
    request: Request,
    attendance_id: int = Form(...),
    decision: str = Form(...)
):
    manager = require_verification_manager(request)
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))

    try:
        verify_attendance(
            manager["id"],
            attendance_id,
            decision,
            physical_confirmed=(
                form.get("physical_confirmed") == "yes"
            )
        )
        message = f"Attendance {decision.lower()} successfully."
    except ValueError as error:
        message = str(error)

    return HTMLResponse(page(f"""
        <div class="card">
            <h2>Verification Result</h2>
            <p>{escape(message)}</p>
            <a href="/manager/attendance">
                Back to Pending Attendance
            </a>
        </div>
    """))

# MRA BRANCH TRANSFER MANAGEMENT

from transfer_service import create_transfer, list_transfers
from attendance_service import BRANCHES
from datetime import date
from html import escape as html_escape


def require_transfer_gm(request):
    employee = attendance_employee(request)

    if employee["role"] != "GM":
        raise HTTPException(
            status_code=403,
            detail="General Manager access required"
        )

    return employee


@app.get("/transfers", response_class=HTMLResponse)
def transfers_page(request: Request):
    require_transfer_gm(request)

    with get_connection() as conn:
        employees = conn.execute("""
            SELECT id, full_name, branch
            FROM employees
            WHERE is_active = 1
              AND role IN ('STAFF', 'MANAGER')
            ORDER BY full_name
        """).fetchall()

    employee_options = ""

    for employee in employees:
        employee_options += (
            f'<option value="{int(employee["id"])}">'
            f'{html_escape(employee["full_name"])}'
            f' ({html_escape(employee["branch"] or "Unassigned")})'
            '</option>'
        )

    branch_options = ""

    for branch in BRANCHES:
        branch_options += (
            f'<option value="{html_escape(branch)}">'
            f'{html_escape(branch)}</option>'
        )

    transfer_rows = ""

    for transfer in list_transfers():
        transfer_rows += f"""
        <tr>
            <td>{html_escape(transfer['full_name'])}</td>
            <td>{html_escape(transfer['home_branch'] or '')}</td>
            <td>{html_escape(transfer['destination_branch'])}</td>
            <td>{html_escape(transfer['start_date'])}</td>
            <td>{html_escape(transfer['end_date'])}</td>
            <td>{html_escape(transfer['status'])}</td>
        </tr>
        """

    if not transfer_rows:
        transfer_rows = """
        <tr>
            <td colspan="6">No transfers recorded yet.</td>
        </tr>
        """

    today = date.today().isoformat()

    return HTMLResponse(page(f"""
        <div class="card" style="max-width:1050px">
            <h1>Branch Transfer Management</h1>
            <p>General Manager Approval</p>

            <h2>Approve Temporary Transfer</h2>

            <form method="post" action="/transfers/create">
                {csrf_input(request)}
                <label>Employee</label>
                <select name="employee_id" required>
                    <option value="">Select Employee</option>
                    {employee_options}
                </select>

                <label>Destination Branch</label>
                <select name="destination_branch" required>
                    <option value="">Select Branch</option>
                    {branch_options}
                </select>

                <label>Start Date</label>
                <input type="date" name="start_date"
                       value="{today}" required>

                <label>End Date</label>
                <input type="date" name="end_date"
                       value="{today}" required>

                <button type="submit">
                    Approve Transfer
                </button>
            </form>

            <h2>Transfer History</h2>

            <div style="overflow-x:auto">
                <table border="1" cellpadding="8"
                       style="border-collapse:collapse;width:100%">
                    <tr>
                        <th>Employee</th>
                        <th>Home Branch</th>
                        <th>Destination</th>
                        <th>Start</th>
                        <th>End</th>
                        <th>Status</th>
                    </tr>
                    {transfer_rows}
                </table>
            </div>

            <p><a href="/dashboard">Back to Dashboard</a></p>
        </div>
    """))


@app.post("/transfers/create", response_class=HTMLResponse)
async def create_transfer_route(
    request: Request,
    employee_id: int = Form(...),
    destination_branch: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...)
):
    gm = require_transfer_gm(request)
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))

    try:
        transfer_id = create_transfer(
            gm["id"],
            employee_id,
            destination_branch,
            start_date,
            end_date
        )
        message = (
            f"Transfer #{transfer_id} approved successfully."
        )
    except ValueError as error:
        message = str(error)

    return HTMLResponse(page(f"""
        <div class="card">
            <h2>Transfer Result</h2>
            <p>{html_escape(message)}</p>
            <a href="/transfers">Back to Transfers</a>
        </div>
    """))

# MRA ATTENDANCE REPORTS DASHBOARD

from report_service import (
    attendance_report,
    advanced_attendance_report,
    resolve_report_period
)
from datetime import date as report_date_class
from html import escape as report_escape




# MRA ATTENDANCE CSV EXPORT

@app.get("/reports/export.csv")
def export_attendance_csv(
    request: Request,
    report_date: str = "",
    period: str = "daily",
    start_date: str = "",
    end_date: str = "",
    branch_filter: str = "",
    employee_filter: str = ""
):
    import csv
    import io
    from datetime import datetime, timezone
    from fastapi.responses import Response

    viewer = attendance_employee(request)

    if viewer["role"] not in ("GM", "MANAGER"):
        raise HTTPException(
            status_code=403,
            detail="Reporting access required"
        )

    selected_date = (
        report_date or report_date_class.today().isoformat()
    )

    try:
        range_start, range_end = resolve_report_period(
            period=period,
            selected_date=selected_date,
            start_date=start_date or None,
            end_date=end_date or None
        )

        selected_branch = branch_filter.strip()
        selected_employee = employee_filter.strip()

        if selected_branch and selected_branch not in BRANCHES:
            raise ValueError("Invalid MRA branch.")

        employee_id = None

        if selected_employee:
            try:
                employee_id = int(selected_employee)
            except ValueError:
                raise ValueError("Invalid employee selection.")

            if employee_id <= 0:
                raise ValueError("Invalid employee selection.")

        if viewer["role"] == "MANAGER":
            selected_branch = viewer["branch"]

        summary, records = advanced_attendance_report(
            viewer["id"],
            range_start,
            range_end,
            branch=selected_branch or None,
            employee_id=employee_id
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    def safe_csv_value(value):
        text = "" if value is None else str(value)

        if text.lstrip().startswith(
            ("=", "+", "-", "@", "\t", "\r", "\n")
        ):
            return "'" + text

        return text

    def parse_timestamp(value):
        if not value:
            return None

        try:
            parsed = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(timezone.utc)

        except (ValueError, TypeError):
            return None

    output = io.StringIO(newline="")
    writer = csv.writer(output)

    writer.writerow([
        "Employee",
        "Branch",
        "Clock In (Gambia Time)",
        "Clock Out (Gambia Time)",
        "Duration (Hours)",
        "Verification Status"
    ])

    for record in records:
        clock_in = parse_timestamp(record["clock_in"])
        clock_out = parse_timestamp(record["clock_out"])

        duration_hours = ""

        if clock_in and clock_out:
            seconds = (clock_out - clock_in).total_seconds()

            if seconds >= 0:
                duration_hours = round(seconds / 3600, 4)

        writer.writerow([
            safe_csv_value(record["full_name"]),
            safe_csv_value(record["branch"]),
            clock_in.strftime("%Y-%m-%d %H:%M:%S")
                if clock_in else "",
            clock_out.strftime("%Y-%m-%d %H:%M:%S")
                if clock_out else "",
            duration_hours,
            safe_csv_value(
                record["verification_status"] or "UNKNOWN"
            )
        ])

    filename = (
        f"MRA_Attendance_{range_start}_to_{range_end}.csv"
    )

    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            ),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff"
        }
    )

@app.get("/reports", response_class=HTMLResponse)
def attendance_reports_page(
    request: Request,
    report_date: str = "",
    period: str = "daily",
    start_date: str = "",
    end_date: str = "",
    branch_filter: str = "",
    employee_filter: str = ""
):
    viewer = attendance_employee(request)

    if viewer["role"] not in ("GM", "MANAGER"):
        raise HTTPException(
            status_code=403,
            detail="Reporting access required"
        )

    selected_date = report_date or report_date_class.today().isoformat()

    try:
        range_start, range_end = resolve_report_period(
            period=period,
            selected_date=selected_date,
            start_date=start_date or None,
            end_date=end_date or None
        )

        selected_branch = branch_filter.strip()
        selected_employee = employee_filter.strip()

        if selected_branch and selected_branch not in BRANCHES:
            raise ValueError("Invalid MRA branch.")

        if selected_employee:
            try:
                employee_id = int(selected_employee)
            except ValueError:
                raise ValueError("Invalid employee selection.")

            if employee_id <= 0:
                raise ValueError("Invalid employee selection.")
        else:
            employee_id = None

        if viewer["role"] == "MANAGER":
            selected_branch = viewer["branch"]

        summary, records = advanced_attendance_report(
            viewer["id"],
            range_start,
            range_end,
            branch=selected_branch or None,
            employee_id=employee_id
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )


    available_branches = (
        BRANCHES
        if viewer["role"] == "GM"
        else [viewer["branch"]]
    )

    report_branch_options = ""

    if viewer["role"] == "GM":
        report_branch_options += '<option value="">All Branches</option>'

    for branch_name in available_branches:
        selected = (
            " selected"
            if branch_name == selected_branch
            else ""
        )

        safe_branch = report_escape(str(branch_name))

        report_branch_options += (
            f'<option value="{safe_branch}"{selected}>'
            f'{safe_branch}</option>'
        )

    with get_connection() as conn:
        if viewer["role"] == "GM":
            report_employees = conn.execute("""
                SELECT id, full_name, branch
                FROM employees
                ORDER BY full_name
            """).fetchall()
        else:
            report_employees = conn.execute("""
                SELECT id, full_name, branch
                FROM employees
                WHERE branch = ?
                ORDER BY full_name
            """, (viewer["branch"],)).fetchall()

    report_employee_options = (
        '<option value="">All Employees</option>'
    )

    for report_employee in report_employees:
        employee_value = str(report_employee["id"])

        selected = (
            " selected"
            if employee_value == selected_employee
            else ""
        )

        safe_name = report_escape(
            str(report_employee["full_name"])
        )

        report_employee_options += (
            f'<option value="{employee_value}"{selected}>'
            f'{safe_name}</option>'
        )

    from datetime import datetime, timezone

    def report_parse_timestamp(value):
        if not value:
            return None

        try:
            parsed = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None

    def report_clock_format(value):
        parsed = report_parse_timestamp(value)

        if parsed is None:
            return "—" if not value else str(value)

        return parsed.strftime("%d %b %Y, %I:%M:%S %p")

    def report_shift_duration(clock_in, clock_out):
        start = report_parse_timestamp(clock_in)
        end = report_parse_timestamp(clock_out)

        if start is None or end is None:
            return "—"

        seconds = int((end - start).total_seconds())

        if seconds < 0:
            return "Invalid duration"

        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours:
            return f"{hours}h {minutes}m {seconds}s"

        if minutes:
            return f"{minutes}m {seconds}s"

        return f"{seconds}s"

    rows = ""

    for record in records:
        clock_in = record["clock_in"]
        clock_out = record["clock_out"]
        status = str(
            record["verification_status"] or "UNKNOWN"
        ).upper()

        display_in = report_clock_format(clock_in)

        display_out = (
            report_clock_format(clock_out)
            if clock_out
            else "Still clocked in"
        )

        duration = report_shift_duration(
            clock_in,
            clock_out
        )

        status_colors = {
            "APPROVED": ("#166534", "#DCFCE7"),
            "PENDING": ("#92400E", "#FEF3C7"),
            "REJECTED": ("#991B1B", "#FEE2E2"),
        }

        foreground, background = status_colors.get(
            status,
            ("#374151", "#F3F4F6")
        )

        status_html = (
            f'<span style="display:inline-block;'
            f'padding:5px 10px;border-radius:999px;'
            f'font-weight:600;color:{foreground};'
            f'background:{background};">'
            f'{report_escape(status)}</span>'
        )

        rows += f"""
        <tr>
            <td>{report_escape(str(record['full_name']))}</td>
            <td>{report_escape(str(record['branch']))}</td>
            <td>{report_escape(display_in)}</td>
            <td>{report_escape(display_out)}</td>
            <td>{report_escape(duration)}</td>
            <td>{status_html}</td>
        </tr>
        """

    if not rows:
        rows = """
        <tr>
            <td colspan="6" style="text-align:center;">
                No attendance records for this period.
            </td>
        </tr>
        """

    return HTMLResponse(page(f"""
        <div class="card" style="max-width:1100px">
            <h1>MRA Attendance Reports</h1>

            <p>
                Welcome, {report_escape(str(viewer['full_name']))}
            </p>

            <p>
                Access: {report_escape(str(viewer['role']))}
            </p>

            <form method="get" action="/reports">
                    <label>Reporting Period</label>
                    <select name="period" required>
                        <option value="daily" {"selected" if period == "daily" else ""}>Daily</option>
                        <option value="weekly" {"selected" if period == "weekly" else ""}>Weekly</option>
                        <option value="monthly" {"selected" if period == "monthly" else ""}>Monthly</option>
                        <option value="custom" {"selected" if period == "custom" else ""}>Custom Range</option>
                    </select>

                    <label>Reference Date</label>
                    <input type="date"
                           name="report_date"
                           value="{report_escape(selected_date)}">

                    <label>Custom Start Date</label>
                    <input type="date"
                           name="start_date"
                           value="{report_escape(start_date)}">

                    <label>Custom End Date</label>
                    <input type="date"
                           name="end_date"
                           value="{report_escape(end_date)}">

                    <label>Branch</label>
                    <select name="branch_filter">
                        {report_branch_options}
                    </select>

                    <label>Employee</label>
                    <select name="employee_filter">
                        {report_employee_options}
                    </select>

                    <button type="submit">View Report</button>

                    <button
                        type="submit"
                        formaction="/reports/export.csv"
                        formmethod="get"
                        style="background:#EF4655;"
                    >
                        Download CSV
                    </button>
                </form>

                <p>
                    Reporting period:
                    {report_escape(range_start)}
                    to
                    {report_escape(range_end)}
                </p>

            <h2>Attendance Summary</h2>

            <div style="display:grid;
                        grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
                        gap:12px;">

                <div class="card">
                    <h3>Total Clock-ins</h3>
                    <h2>{summary['total']}</h2>
                </div>

                <div class="card">
                    <h3>Approved</h3>
                    <h2>{summary['approved']}</h2>
                </div>

                <div class="card">
                    <h3>Pending</h3>
                    <h2>{summary['pending']}</h2>
                </div>

                <div class="card">
                    <h3>Rejected</h3>
                    <h2>{summary['rejected']}</h2>
                </div>

                <div class="card">
                    <h3>Open Shifts</h3>
                    <h2>{summary['open_shifts']}</h2>
                </div>

                <div class="card">
                    <h3>Completed Shifts</h3>
                    <h2>{summary['completed_shifts']}</h2>
                </div>

                <div class="card">
                    <h3>Recorded Working Hours</h3>
                    <h2>{summary['working_hours']:.2f} hrs</h2>
                    <p style="font-size:12px;opacity:0.75;">
                        Includes completed shifts regardless of approval.
                        Not approved payroll hours.
                    </p>
                </div>
            </div>

            <h2>Employee Attendance Records</h2>

            <div style="overflow-x:auto">
                <table border="1" cellpadding="10"
                       style="border-collapse:collapse;width:100%">
                    <tr>
                        <th>Employee</th>
                        <th>Branch</th>
                        <th>Clock In</th>
                        <th>Clock Out</th>
                        <th>Duration</th>
                        <th>Status</th>
                    </tr>
                    {rows}
                </table>
            </div>

            <p><a href="/dashboard">Back to Dashboard</a></p>
        </div>
    """))


# MRA LOGIN RATE LIMITER
app.state.limiter = mra_login_limiter
app.add_middleware(SlowAPIMiddleware)

# BRANCH MANAGEMENT

@app.get("/branches", response_class=HTMLResponse)
def branches_page(request: Request):
    require_gm(request)

    with get_connection() as conn:
        branches = conn.execute("""
            SELECT id, name, latitude, longitude,
                   gps_radius_metres, is_active
            FROM branches
            ORDER BY id
        """).fetchall()

    rows = ""

    for branch in branches:
        name = escape(branch["name"])

        if branch["latitude"] is None:
            gps = "Not configured"
        else:
            gps = (
                f'{branch["latitude"]}, '
                f'{branch["longitude"]}'
            )

        status = "Active" if branch["is_active"] else "Inactive"

        rows += f"""
            <tr>
                <td>{name}</td>
                <td>{gps}</td>
                <td>{branch["gps_radius_metres"]} m</td>
                <td>{status}</td>
                <td><a href="/branches/{branch['id']}/edit">Edit</a></td>
            </tr>
        """

    return HTMLResponse(page(f"""
        <div class="card">
            <h1>Branch Management</h1>
            <p>MRA Trading Company Limited</p>

            <div style="overflow-x:auto;">
                <table>
                    <tr>
                        <th>Branch</th>
                        <th>GPS Coordinates</th>
                        <th>Radius</th>
                        <th>Status</th>
                        <th>Action</th>
                    </tr>
                    {rows}
                </table>
            </div>

            <p><a href="/dashboard">Back to Dashboard</a></p>
        </div>
    """))

# BRANCH MANAGEMENT

# EDIT BRANCH SETTINGS

@app.get("/branches/{branch_id}/edit", response_class=HTMLResponse)
def edit_branch_page(branch_id: int, request: Request):
    require_gm(request)

    with get_connection() as conn:
        branch = conn.execute(
            """
            SELECT id, name, latitude, longitude,
                   gps_radius_metres, is_active
            FROM branches
            WHERE id = ?
            """,
            (branch_id,)
        ).fetchone()

    if branch is None:
        raise HTTPException(status_code=404, detail="Branch not found")

    name = escape(branch["name"])
    latitude = branch["latitude"]
    longitude = branch["longitude"]
    radius = branch["gps_radius_metres"]

    return HTMLResponse(page(f"""
        <div class="card">
            <h1>Edit Branch Settings</h1>
            <h2>{name}</h2>

            <form method="post" action="/branches/{branch_id}/edit">
                {csrf_input(request)}

                <label>Latitude</label>
                <input type="number" name="latitude"
                       step="any" min="-90" max="90"
                       value="{latitude if latitude is not None else ''}"
                       placeholder="e.g. 13.464111">

                <label>Longitude</label>
                <input type="number" name="longitude"
                       step="any" min="-180" max="180"
                       value="{longitude if longitude is not None else ''}"
                       placeholder="e.g. -16.703667">

                <label>Attendance Radius (metres)</label>
                <input type="number" name="gps_radius_metres"
                       min="50" max="500" required
                       value="{radius}">

                <button type="submit">Save Changes</button>
            </form>

            <p>Leave both GPS fields blank if the branch
               coordinates have not yet been collected.</p>

            <p><a href="/branches">Back to Branch Management</a></p>
        </div>
    """))

# SAVE BRANCH SETTINGS

@app.post("/branches/{branch_id}/edit", response_class=HTMLResponse)
async def save_branch_settings(branch_id: int, request: Request):
    require_gm(request)

    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))

    latitude_text = str(form.get("latitude", "")).strip()
    longitude_text = str(form.get("longitude", "")).strip()
    radius_text = str(form.get("gps_radius_metres", "")).strip()

    if bool(latitude_text) != bool(longitude_text):
        raise HTTPException(
            status_code=400,
            detail="Enter both latitude and longitude, or leave both blank."
        )

    latitude = None
    longitude = None

    if latitude_text:
        try:
            latitude = float(latitude_text)
            longitude = float(longitude_text)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="GPS coordinates must be valid numbers."
            )

        import math

        if not (
            math.isfinite(latitude)
            and math.isfinite(longitude)
            and -90 <= latitude <= 90
            and -180 <= longitude <= 180
        ):
            raise HTTPException(
                status_code=400,
                detail="GPS coordinates are outside the permitted range."
            )

    try:
        radius = int(radius_text)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Attendance radius must be a whole number."
        )

    if not 50 <= radius <= 500:
        raise HTTPException(
            status_code=400,
            detail="Attendance radius must be between 50 and 500 metres."
        )

    with get_connection() as conn:
        result = conn.execute(
            """
            UPDATE branches
            SET latitude = ?,
                longitude = ?,
                gps_radius_metres = ?
            WHERE id = ?
            """,
            (latitude, longitude, radius, branch_id)
        )

        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Branch not found."
            )

        conn.commit()

    return HTMLResponse(page("""
        <div class="card">
            <h1>Branch Settings Saved</h1>
            <p>The branch GPS settings have been updated successfully.</p>
            <p><a href="/branches">Back to Branch Management</a></p>
        </div>
    """))



@app.post("/manager/gps-exceptions/review", response_class=HTMLResponse)
async def manager_gps_exception_review(
    request: Request,
    request_id: int = Form(...),
    decision: str = Form(...)
):
    manager = require_verification_manager(request)
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))

    physical_confirmed = (
        form.get("physical_confirmed") == "yes"
    )
    notes = str(form.get("notes") or "")

    try:
        review_gps_exception(
            manager_id=manager["id"],
            request_id=request_id,
            decision=decision,
            physical_confirmed=physical_confirmed,
            notes=notes
        )
        message = "GPS Exception Request reviewed successfully."
    except ValueError as error:
        message = str(error)

    return HTMLResponse(page(f"""
        <div class="card">
            <h2>GPS Exception Review Result</h2>
            <p>{escape(message)}</p>
            <p>
                <a href="/manager/gps-exceptions">
                    Back to GPS Exception Requests
                </a>
            </p>
        </div>
    """))


@app.get("/manager/gps-exceptions", response_class=HTMLResponse)
def manager_gps_exceptions_page(request: Request):
    manager = require_verification_manager(request)

    try:
        records = pending_gps_exceptions(manager["id"])
    except ValueError as error:
        raise HTTPException(status_code=403, detail=str(error))

    rows = ""

    for record in records:
        rows += f"""
        <tr>
            <td>{escape(record['full_name'])}</td>
            <td>{escape(record['branch'])}</td>
            <td>{escape(record['requested_at'])}</td>
            <td>{record['gps_accuracy']} m</td>
            <td>{record['gps_distance']} m</td>
            <td>{escape(record['reason'])}</td>
            <td>
                <form action="/manager/gps-exceptions/review"
                      method="post">
                    {csrf_input(request)}
                    <input type="hidden"
                           name="request_id"
                           value="{int(record['id'])}">

                    <label style="display:block;margin-bottom:8px">
                        <input type="checkbox"
                               name="physical_confirmed"
                               value="yes">
                        I have physically confirmed this employee
                        is present at the assigned branch.
                    </label>

                    <label style="display:block;margin-bottom:8px">
                        Review Notes:
                        <textarea name="notes"
                                  maxlength="500"
                                  rows="2"
                                  style="width:100%"></textarea>
                    </label>

                    <button type="submit"
                            name="decision"
                            value="APPROVED">
                        Approve
                    </button>

                    <button type="submit"
                            name="decision"
                            value="REJECTED"
                            style="background:#b42318">
                        Reject
                    </button>
                </form>
            </td>
        </tr>
        """

    if not rows:
        rows = """
        <tr>
            <td colspan="7">
                No pending GPS Exception Requests.
            </td>
        </tr>
        """

    return HTMLResponse(page(f"""
        <div class="card" style="max-width:1100px">
            <h1>GPS Exception Requests</h1>

            <p>Manager: {escape(manager['full_name'])}</p>
            <p>Role: {escape(manager['role'])}</p>

            <p>
                GPS Exception Requests require independent
                physical presence verification before approval.
            </p>

            <div style="overflow-x:auto">
                <table border="1" cellpadding="10"
                       style="border-collapse:collapse;width:100%">
                    <tr>
                        <th>Employee</th>
                        <th>Branch</th>
                        <th>Requested At (UTC)</th>
                        <th>GPS Accuracy</th>
                        <th>GPS Distance</th>
                        <th>Reason</th>
                        <th>Manager Review</th>
                    </tr>
                    {rows}
                </table>
            </div>

            <p><a href="/manager/attendance">
                Back to Manager Attendance
            </a></p>
        </div>
    """))

# Redirect expired browser sessions to login.
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def handle_authentication_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401 and request.method in ("GET", "HEAD"):
        accepts_html = "text/html" in request.headers.get("accept", "")
        if accepts_html:
            return RedirectResponse("/", status_code=303)

    return await http_exception_handler(request, exc)
