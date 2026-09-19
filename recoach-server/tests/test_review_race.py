import json
import pytest
from starlette.requests import Request
from app.routes.turns import _handle_duplicate_key
from app.services import turns

@pytest.mark.parametrize("status", ["streaming", "completed", "error"])
def test_collision_rejects_different_payload(monkeypatch, status):
    existing = {"id": "turn_old", "status": status,
                "request_json": json.dumps({"message": {"content": "original"}, "locale": "zh-CN"}),
                "presentation_json": "{}"}
    monkeypatch.setattr(turns, "find_by_client_key", lambda *args: existing)
    def must_not_restart(*args):
        pytest.fail("conflicting request must not restart canonical turn")
    monkeypatch.setattr(turns, "restart_turn", must_not_restart)
    request = Request({"type": "http", "headers": []})
    response = _handle_duplicate_key(request, "session", "client", {"message": {"content": "different"}, "locale": "zh-CN"})
    assert response.status_code == 409
    assert json.loads(response.body)["error"]["code"] == "TURN_CONFLICT"
