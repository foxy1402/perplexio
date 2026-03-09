import hashlib
import hmac

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.settings import (
    AUTH_COOKIE_NAME,
    AUTH_COOKIE_SECURE,
    AUTH_PASSWORD,
    AUTH_SESSION_MAX_AGE_SECONDS,
    AUTH_SESSION_SECRET,
)


def auth_enabled() -> bool:
    return bool(AUTH_PASSWORD)


def _session_token() -> str:
    raw = f"{AUTH_PASSWORD}|{AUTH_SESSION_SECRET}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def is_authenticated_request(request: Request) -> bool:
    if not auth_enabled():
        return True
    token = request.cookies.get(AUTH_COOKIE_NAME, "")
    return hmac.compare_digest(token, _session_token())


def require_auth(request: Request) -> None:
    if not is_authenticated_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


def unauthorized_response() -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": "Unauthorized"})


def login_success_response(auth_enabled_flag: bool) -> JSONResponse:
    resp = JSONResponse({"ok": True, "auth_enabled": auth_enabled_flag})
    resp.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=_session_token(),
        max_age=AUTH_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
    )
    return resp


def logout_response() -> JSONResponse:
    resp = JSONResponse({"ok": True, "auth_enabled": auth_enabled()})
    resp.delete_cookie(
        key=AUTH_COOKIE_NAME,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
    )
    return resp


def verify_password(candidate: str) -> bool:
    if not auth_enabled():
        return True
    return hmac.compare_digest(candidate or "", AUTH_PASSWORD)
