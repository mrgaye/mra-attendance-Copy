import secrets
import hmac
from html import escape
from fastapi import HTTPException


def get_csrf_token(request):
    token = request.session.get("_csrf_token")

    if not token:
        token = secrets.token_urlsafe(32)
        request.session["_csrf_token"] = token

    return token


def csrf_input(request):
    token = get_csrf_token(request)

    return (
        '<input type="hidden" name="csrf_token" value="'
        + escape(token, quote=True)
        + '">'
    )


def validate_csrf(request, submitted_token):
    expected = request.session.get("_csrf_token")

    if (
        not expected
        or not submitted_token
        or not hmac.compare_digest(expected, submitted_token)
    ):
        raise HTTPException(
            status_code=403,
            detail="Security verification failed. Please reload the page."
        )

    return True
