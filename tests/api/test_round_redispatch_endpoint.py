"""``POST /api/v1/deliveries/{round_id}/redispatch`` over HTTP (§8.7.4, A-13).

The endpoint is a translation table and an auth check, so that is what these
test: which refusal wears which status code, and what the operator is told to
do next. The service behind it is exercised in
``tests/task_orchestration/test_round_redispatch.py`` against the real
orchestrator; here it is a double, because mounting the whole execution plane
to assert a 503's wording would test the plane instead of the wording.

The status codes are not cosmetic. A 503 says "press it again when the plane is
back" and a 409 says "pressing again will not help"; A-10 was one wrong code
telling an operator to file a bug about a round that only needed the button.
Nothing here reaches a network.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from repomesh.api.router import api_router
from repomesh.modules.collaboration.contracts import CollaborationRouteUnavailable
from repomesh.modules.task_orchestration.contracts import (
    RedispatchScope,
    RoundRedispatch,
    TaskPublicationUnavailable,
)
from repomesh.modules.task_orchestration.domain import (
    RoundNotDispatchable,
    TaskDenied,
    TaskNotFound,
)
from repomesh.settings import get_settings

HEADERS = {"Authorization": "Bearer internal-secret"}
ROUND_ID = uuid4()


class StubRedispatch:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises
        self.calls: list[tuple] = []

    async def execute(
        self, round_id, *, attempt: str, scope: RedispatchScope = RedispatchScope.UNFINISHED
    ) -> RoundRedispatch:
        self.calls.append((round_id, attempt, scope))
        if self.raises is not None:
            raise self.raises
        return RoundRedispatch(
            round_id=round_id,
            attempt=attempt,
            scope=scope,
            task_ids=(ROUND_ID,),
            reopened_task_ids=(ROUND_ID,) if scope is RedispatchScope.RERUN else (),
            settled_task_ids=(),
        )


class StubContainer:
    def __init__(self, service) -> None:
        self._service = service

    def round_redispatch_service(self):
        return self._service


def _client(service, monkeypatch) -> TestClient:
    monkeypatch.setenv("REPOMESH_AGENT_ACTION_TOKEN", "internal-secret")
    get_settings.cache_clear()
    application = FastAPI()
    application.include_router(api_router)
    application.state.container = StubContainer(service)
    return TestClient(application)


def _post(client, body=None):
    return client.post(
        f"/api/v1/deliveries/{ROUND_ID}/redispatch",
        json=body or {"idempotency_key": "console-redispatch-1"},
        headers=HEADERS,
    )


def test_a_re_dispatch_reports_what_it_did(monkeypatch) -> None:
    service = StubRedispatch()
    response = _post(_client(service, monkeypatch))

    assert response.status_code == 200
    payload = response.json()
    assert payload["round_id"] == str(ROUND_ID)
    assert payload["attempt"] == "console-redispatch-1"
    assert payload["task_ids"] == [str(ROUND_ID)]
    # The request key *is* the attempt token; the console mints one per press.
    # And the default scope is the one that writes nothing.
    assert service.calls == [(ROUND_ID, "console-redispatch-1", RedispatchScope.UNFINISHED)]
    assert payload["scope"] == "unfinished"
    assert payload["reopened_task_ids"] == []


def test_a_rerun_must_be_asked_for_explicitly(monkeypatch) -> None:
    """The scope that writes task rows is never the default.

    ``unfinished`` costs a duplicate notification when pressed by mistake;
    ``rerun`` un-succeeds a batch and sends finished work back out. Those are
    not the same risk, so they are not the same button.
    """

    service = StubRedispatch()
    response = _post(
        _client(service, monkeypatch),
        body={"idempotency_key": "console-redispatch-1", "scope": "rerun"},
    )

    assert response.status_code == 200
    assert service.calls == [(ROUND_ID, "console-redispatch-1", RedispatchScope.RERUN)]
    # The receipt names what was sent back to work, separately from what was
    # merely re-told — the operator has to be able to see the write happened.
    assert response.json()["reopened_task_ids"] == [str(ROUND_ID)]


def test_an_unknown_scope_is_refused(monkeypatch) -> None:
    service = StubRedispatch()
    response = _post(
        _client(service, monkeypatch),
        body={"idempotency_key": "k", "scope": "everything"},
    )

    assert response.status_code == 422
    assert service.calls == []


def test_the_token_is_required(monkeypatch) -> None:
    client = _client(StubRedispatch(), monkeypatch)
    response = client.post(
        f"/api/v1/deliveries/{ROUND_ID}/redispatch",
        json={"idempotency_key": "console-redispatch-1"},
    )
    assert response.status_code == 401


def test_an_unknown_round_is_404(monkeypatch) -> None:
    service = StubRedispatch(raises=TaskNotFound("round does not exist: x"))
    assert _post(_client(service, monkeypatch)).status_code == 404


@pytest.mark.parametrize(
    "detail",
    [
        "round x has no tasks yet; it was never fully materialised",
        "every task of round x has already finished (3 task(s))",
    ],
)
def test_a_round_with_nothing_to_dispatch_is_409(detail, monkeypatch) -> None:
    """409, not 503: pressing again changes neither shape.

    And the server's own sentence is passed through, because which of the two
    it is decides where the operator goes next — materialize, or nowhere.
    """

    service = StubRedispatch(raises=RoundNotDispatchable(detail))
    response = _post(_client(service, monkeypatch))

    assert response.status_code == 409
    assert detail in response.json()["detail"]


def test_a_store_that_will_not_take_the_package_is_503(monkeypatch) -> None:
    """A-10's translation, reused — and it must not claim work was re-sent."""

    service = StubRedispatch(
        raises=TaskPublicationUnavailable(
            "S3 operation failed; code: InvalidAccessKeyId"
        )
    )
    response = _post(_client(service, monkeypatch))

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "InvalidAccessKeyId" in detail, "the store's own words are the actionable half"
    assert "nothing was re-sent" in detail


def test_a_missing_room_is_503(monkeypatch) -> None:
    service = StubRedispatch(raises=CollaborationRouteUnavailable("AgentTeams room is not ready"))
    response = _post(_client(service, monkeypatch))

    assert response.status_code == 503
    assert "room is not ready" in response.json()["detail"]


def test_a_denial_is_403(monkeypatch) -> None:
    service = StubRedispatch(raises=TaskDenied("worker is not on this team"))
    assert _post(_client(service, monkeypatch)).status_code == 403


def test_an_unconfigured_execution_plane_is_503(monkeypatch) -> None:
    """No messenger, no orchestrator, nothing to dispatch through.

    A deployment problem, and the operator fixes it in the deployment — so a
    503 rather than the 500 an unguarded ``None`` would have produced.
    """

    response = _post(_client(None, monkeypatch))

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_an_empty_key_is_refused_before_the_service(monkeypatch) -> None:
    """The attempt token is the whole idempotency story; it may not be blank."""

    service = StubRedispatch()
    response = _post(_client(service, monkeypatch), body={"idempotency_key": ""})

    assert response.status_code == 422
    assert service.calls == []
