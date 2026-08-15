import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

# auto_error=False: we want to decide 503-vs-401 ourselves rather than let
# FastAPI short-circuit on a missing Authorization header (which would hide
# the "auth not configured" case behind a misleading 401).
security = HTTPBasic(auto_error=False)


async def require_admin(
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    if not settings.admin_password:
        # Fail closed: an unconfigured admin password must never be treated
        # as "no auth required".
        raise HTTPException(status_code=503, detail="admin auth not configured")

    unauthorized = HTTPException(
        status_code=401,
        detail="invalid credentials",
        headers={"WWW-Authenticate": "Basic"},
    )
    if credentials is None:
        raise unauthorized

    user_ok = secrets.compare_digest(credentials.username, settings.admin_user)
    pass_ok = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (user_ok and pass_ok):
        raise unauthorized

    return credentials.username
