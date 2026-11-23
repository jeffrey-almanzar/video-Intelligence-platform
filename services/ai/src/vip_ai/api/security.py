"""Service-to-service auth for the internal API: NestJS sends `Authorization: Bearer <token>`."""

import hmac

from fastapi import HTTPException, Request, status


def require_service_token(request: Request) -> None:
    expected = request.app.state.settings.ai_service_token.get_secret_value()
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
    if not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid service token"
        )
