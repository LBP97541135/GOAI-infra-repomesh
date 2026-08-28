"""Enrollment and binding v2: one added field, and everything it decides.

``contracts/agent-bridge/v2`` is v1 plus ``role``, so the tests that matter most
here are the ones about what did *not* change. A deployed worker Bridge keeps
its v1 document indefinitely, calls the v1 endpoint, and sends the same bytes it
always sent; the second version exists because a Repository Leader has no v1
representation at all, not because v1 was wrong.

All six frozen fixtures are consumed, the two invalid ones included — a contract
that publishes documents which "must be rejected" is only half-checked by a
suite that never tries to read them.

The preflight half is exercised twice on purpose: against a mock transport,
where the request itself is the assertion, and against RepoMesh's real v2 route
in process, where the two sides have to agree without either being a copy of the
other. Nothing here binds a port or reaches a network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from api.test_external_member_binding import (
    CONTROL_TOKEN,
    LEADER_ID,
    _client,
    _leader_scene,
)

from repomesh_agent_bridge.adapters.repomesh_binding import (
    BINDING_PATH,
    BINDING_V2_PATH,
    RepoMeshBindingAdapter,
)
from repomesh_agent_bridge.cli import EXIT_STARTUP_REFUSED, main
from repomesh_agent_bridge.contracts import (
    BINDING_SCHEMA_VERSION,
    BINDING_V2_SCHEMA_VERSION,
    ENROLLMENT_SCHEMA_VERSION,
    ENROLLMENT_V2_SCHEMA_VERSION,
    BindingRefused,
    EnrollmentInvalid,
    ExternalWorkerEnrollment,
    WorkerBinding,
    read_enrollment,
)

FIXTURES = Path(__file__).parents[2] / "contracts" / "agent-bridge" / "v2" / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def leader_enrollment() -> ExternalWorkerEnrollment:
    return ExternalWorkerEnrollment.from_wire_v2(fixture("enrollment.repository-leader.json"))


def worker_enrollment_v2() -> ExternalWorkerEnrollment:
    return ExternalWorkerEnrollment.from_wire_v2(fixture("enrollment.worker.json"))


# ---------------------------------------------------------------------------
# The four valid fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "model", "role"),
    [
        ("enrollment.worker.json", ExternalWorkerEnrollment, "worker"),
        ("enrollment.repository-leader.json", ExternalWorkerEnrollment, "repository_leader"),
        ("binding.worker.json", WorkerBinding, "worker"),
        ("binding.repository-leader.json", WorkerBinding, "repository_leader"),
    ],
)
def test_every_valid_v2_fixture_round_trips(name: str, model: Any, role: str) -> None:
    document = fixture(name)
    record = model.from_wire_v2(document)
    assert record.role == role
    assert record.to_wire() == document


def test_a_leader_enrollment_names_the_leader_dm_and_not_a_worker_dm() -> None:
    """The role-aware allowlist, read from the consumer's side."""

    enrollment = leader_enrollment()
    assert enrollment.is_repository_leader
    assert any("leader" in room for room in enrollment.allowed_room_ids)
    assert not any("worker" in room for room in enrollment.allowed_room_ids)


# ---------------------------------------------------------------------------
# The two invalid fixtures — load-bearing, so actually loaded
# ---------------------------------------------------------------------------


def test_an_organization_leader_enrollment_is_refused() -> None:
    """``organization_leader`` is a real RepoMesh role this contract cannot
    express, so a Bridge meeting the string says no rather than serving one."""

    with pytest.raises(EnrollmentInvalid, match="role"):
        ExternalWorkerEnrollment.from_wire_v2(
            fixture("enrollment.invalid-role.organization-leader.json")
        )


def test_a_binding_with_a_malformed_room_is_refused() -> None:
    with pytest.raises(BindingRefused, match="Matrix room id"):
        WorkerBinding.from_wire_v2(fixture("binding.invalid-room.malformed-room-id.json"))


# ---------------------------------------------------------------------------
# The README's round-trip rules
# ---------------------------------------------------------------------------


def test_a_valid_v1_worker_enrollment_upgrades_by_adding_one_field() -> None:
    """"Set schemaVersion to the v2 constant and add role: worker. Nothing else
    changes." Checked in both directions from the same document."""

    v2_document = fixture("enrollment.worker.json")
    v1_document = {
        key: value for key, value in v2_document.items() if key != "role"
    } | {"schemaVersion": ENROLLMENT_SCHEMA_VERSION}

    from_v1 = ExternalWorkerEnrollment.from_wire(v1_document)
    from_v2 = ExternalWorkerEnrollment.from_wire_v2(v2_document)

    assert from_v1.role == from_v2.role == "worker"
    assert from_v1.to_wire() == v1_document
    assert from_v2.to_wire() == v2_document
    # Every field but the two version markers is identical.
    assert from_v1.worker_agent_id == from_v2.worker_agent_id
    assert from_v1.allowed_room_ids == from_v2.allowed_room_ids


def test_a_repository_leader_has_no_v1_representation() -> None:
    """Downgrading one is an error, not a lossy conversion — so the record
    cannot be built at all, rather than being built and quietly reading back as
    a worker somewhere later."""

    with pytest.raises(ValueError, match="no v1 representation"):
        ExternalWorkerEnrollment.from_wire_v2(
            fixture("enrollment.repository-leader.json")
        ).__class__(
            **{
                **{
                    field: getattr(leader_enrollment(), field)
                    for field in (
                        "organization_id",
                        "worker_agent_id",
                        "worker_name",
                        "team_name",
                        "matrix_user_id",
                        "matrix_homeserver_url",
                        "allowed_room_ids",
                        "repomesh_endpoint",
                        "coding_profile",
                        "credential_refs",
                    )
                },
                "role": "repository_leader",
                "schema_version": ENROLLMENT_SCHEMA_VERSION,
            }
        )


def test_a_v1_reader_will_not_read_a_v2_document_and_the_reverse() -> None:
    """Two versions, two readers, no negotiation to get wrong.

    The v1 reader refuses ``role`` as an undeclared field rather than as a
    version mismatch, because v1's schema is closed and that check comes first.
    Either sentence is the same answer — this document is not a v1 enrollment —
    and a caller reaching the v1 reader with a v2 document has bypassed
    ``read_enrollment``, which is what exists to pick between them.
    """

    with pytest.raises(EnrollmentInvalid, match="role"):
        ExternalWorkerEnrollment.from_wire(fixture("enrollment.worker.json"))
    with pytest.raises(BindingRefused, match="schemaVersion"):
        WorkerBinding.from_wire_v2(
            {**fixture("binding.worker.json"), "schemaVersion": BINDING_SCHEMA_VERSION}
        )


def test_read_enrollment_picks_the_version_the_document_declares() -> None:
    assert read_enrollment(fixture("enrollment.worker.json")).schema_version == (
        ENROLLMENT_V2_SCHEMA_VERSION
    )
    v1_document = {
        key: value
        for key, value in fixture("enrollment.worker.json").items()
        if key != "role"
    } | {"schemaVersion": ENROLLMENT_SCHEMA_VERSION}
    assert read_enrollment(v1_document).schema_version == ENROLLMENT_SCHEMA_VERSION


def test_an_enrollment_declaring_neither_version_is_refused_once() -> None:
    with pytest.raises(EnrollmentInvalid, match="schemaVersion"):
        read_enrollment({**fixture("enrollment.worker.json"), "schemaVersion": "v3"})


# ---------------------------------------------------------------------------
# Preflight: which endpoint, and what it sends
# ---------------------------------------------------------------------------


def recording() -> tuple[list[httpx.Request], httpx.MockTransport]:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = (
            fixture("binding.repository-leader.json")
            if "v2" in request.url.path and "role=repository_leader" in str(request.url.query)
            else fixture("binding.worker.json")
        )
        if "v2" not in request.url.path:
            body = {
                key: value for key, value in fixture("binding.worker.json").items()
                if key != "role"
            } | {"schemaVersion": BINDING_SCHEMA_VERSION}
        return httpx.Response(200, json=body)

    return seen, httpx.MockTransport(answer)


async def test_a_v1_enrollment_still_calls_the_v1_endpoint_with_no_role() -> None:
    """The compatibility promise, asserted on the request rather than trusted.

    A deployed worker Bridge must not discover that its Bridge started calling
    somewhere else or sending something new.
    """

    v1_document = {
        key: value
        for key, value in fixture("enrollment.worker.json").items()
        if key != "role"
    } | {"schemaVersion": ENROLLMENT_SCHEMA_VERSION}
    enrollment = ExternalWorkerEnrollment.from_wire(v1_document)
    seen, transport = recording()

    binding = await RepoMeshBindingAdapter(transport=transport).fetch_binding(
        enrollment, credential=None
    )

    assert seen[0].url.path == BINDING_PATH.format(worker_agent_id=enrollment.worker_agent_id)
    assert not seen[0].url.query
    assert binding.schema_version == BINDING_SCHEMA_VERSION
    assert binding.role == "worker"


async def test_a_v2_enrollment_calls_the_v2_endpoint_and_states_its_role() -> None:
    enrollment = leader_enrollment()
    seen, transport = recording()

    binding = await RepoMeshBindingAdapter(transport=transport).fetch_binding(
        enrollment, credential=None
    )

    assert seen[0].url.path == BINDING_V2_PATH.format(worker_agent_id=enrollment.worker_agent_id)
    assert dict(seen[0].url.params) == {"role": "repository_leader"}
    assert binding.schema_version == BINDING_V2_SCHEMA_VERSION
    assert binding.role == "repository_leader"


async def test_a_binding_whose_role_disagrees_is_refused_locally_too() -> None:
    """RepoMesh answers 409 for this and would usually never let it through.

    The check is here anyway because this is the field that decides whether the
    process may be handed a workspace at all: a Bridge that took the answer's
    word for its own role would inherit whatever a mistaken control plane said,
    and every other identity field on this answer is already held to exactly
    this standard.
    """

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture("binding.worker.json"))

    with pytest.raises(BindingRefused, match="role disagrees"):
        await RepoMeshBindingAdapter(transport=httpx.MockTransport(answer)).fetch_binding(
            leader_enrollment(), credential=None
        )


# ---------------------------------------------------------------------------
# Preflight against RepoMesh's real v2 route, in process
# ---------------------------------------------------------------------------


def through(app: object) -> RepoMeshBindingAdapter:
    return RepoMeshBindingAdapter(transport=httpx.ASGITransport(app=app))  # type: ignore[arg-type]


async def test_the_client_and_the_real_route_agree_on_a_leader_binding(monkeypatch) -> None:
    """Consumer and producer, neither a copy of the other, meeting on the freeze."""

    directory, control_plane = _leader_scene()
    client = _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch)
    enrollment = leader_enrollment()

    binding = await through(client.app).fetch_binding(enrollment, credential=CONTROL_TOKEN)

    assert binding.to_wire() == fixture("binding.repository-leader.json")
    assert binding.worker_agent_id == UUID(str(LEADER_ID))


async def test_the_real_route_refuses_a_role_the_directory_disagrees_with(monkeypatch) -> None:
    """The server's 409 for an enrollment/binding role mismatch, seen as the
    ``BindingRefused`` a Bridge actually receives.

    A leader enrollment that claimed to be a worker gets no binding: the role in
    the answer is confirmed from RepoMesh's own directory and is never echoed
    back from what the caller said about itself.
    """

    directory, control_plane = _leader_scene()
    client = _client(directory=directory, control_plane=control_plane, monkeypatch=monkeypatch)
    mistaken = ExternalWorkerEnrollment.from_wire_v2(
        {**fixture("enrollment.repository-leader.json"), "role": "worker"}
    )

    with pytest.raises(BindingRefused, match="409"):
        await through(client.app).fetch_binding(mistaken, credential=CONTROL_TOKEN)


# ---------------------------------------------------------------------------
# The CLI, role-aware (AC-02, first layer)
# ---------------------------------------------------------------------------


def write(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "enrollment.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_a_leader_may_not_be_given_a_workspace(tmp_path, capsys) -> None:
    """The cheapest of AC-02's three layers: before a lock, before a socket,
    before any process exists."""

    enrollment = write(tmp_path, fixture("enrollment.repository-leader.json"))
    workspace = tmp_path / "worktrees"
    workspace.mkdir()

    code = main(
        [
            "run",
            "--enrollment",
            str(enrollment),
            "--state-dir",
            str(tmp_path / "state"),
            "--workspace-root",
            str(workspace),
        ]
    )

    error = capsys.readouterr().err
    assert code == EXIT_STARTUP_REFUSED
    assert "--workspace-root" in error
    assert "repository_leader" in error
    assert "never given a repository" in error


def test_the_refusal_does_not_depend_on_the_directory_existing(tmp_path, capsys) -> None:
    """A leader is refused for being a leader, not for a path that happens to be
    missing — otherwise creating the directory would make the flag work."""

    enrollment = write(tmp_path, fixture("enrollment.repository-leader.json"))

    code = main(
        [
            "run",
            "--enrollment",
            str(enrollment),
            "--workspace-root",
            str(tmp_path / "nothing-here"),
        ]
    )

    assert code == EXIT_STARTUP_REFUSED
    assert "repository_leader" in capsys.readouterr().err


def test_a_v2_worker_enrollment_still_accepts_a_workspace(tmp_path, capsys) -> None:
    """The role gate is about the role and not about the version: a worker under
    v2 behaves exactly as one under v1.

    It gets past the flag check and is stopped later, by the profile/credential
    assembly this fixture cannot satisfy — which is the point, because being
    stopped *there* means ``--workspace-root`` itself was accepted.
    """

    enrollment = write(tmp_path, fixture("enrollment.worker.json"))
    workspace = tmp_path / "worktrees"
    workspace.mkdir()

    code = main(
        [
            "run",
            "--enrollment",
            str(enrollment),
            "--state-dir",
            str(tmp_path / "state"),
            "--workspace-root",
            str(workspace),
        ]
    )

    error = capsys.readouterr().err
    assert code == EXIT_STARTUP_REFUSED
    assert "--workspace-root" not in error
    assert "repository_leader" not in error


def test_check_reports_both_sides_of_the_role(tmp_path, capsys, monkeypatch) -> None:
    """The report shows the claim and the confirmation, because the two agreeing
    is what stage 2 just established."""

    from repomesh_agent_bridge.adapters.memory import InMemoryWorkerBindingPort

    enrollment = write(tmp_path, fixture("enrollment.repository-leader.json"))
    binding = WorkerBinding.from_wire_v2(fixture("binding.repository-leader.json"))

    code = main(
        ["check", "--enrollment", str(enrollment)],
        binding_port_factory=lambda _: InMemoryWorkerBindingPort(binding),
    )

    assert code == 0
    assert "role: repository_leader (RepoMesh confirms: repository_leader)" in (
        capsys.readouterr().out
    )
