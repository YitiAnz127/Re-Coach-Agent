from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..errors import error_payload

from ..services.metrics import summarize
from .sessions import current_user_id_or_error, owned_session

router = APIRouter()


@router.get("/metrics/summary")
def metrics_summary(request: Request, sessionId: str | None = None):
    if sessionId is not None and owned_session(request, sessionId) is None:
        return JSONResponse(status_code=404, content={"error": error_payload("SESSION_NOT_FOUND")})
    user_id, error = current_user_id_or_error(request)
    if error is not None:
        return error
    return {"data": summarize(user_id=user_id, session_id=sessionId)}
