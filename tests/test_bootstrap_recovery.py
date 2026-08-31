from uuid import uuid4

import pytest

from repomesh.integrations.bootstrap import BootstrapRedactor, RetryPolicy


@pytest.mark.asyncio
async def test_retry_policy_succeeds_on_third_attempt(monkeypatch) -> None:
    sleeps = []
    monkeypatch.setattr("asyncio.sleep", lambda delay: _record_sleep(sleeps, delay))
    attempts = 0

    async def action() -> bool:
        nonlocal attempts
        attempts += 1
        return attempts == 3

    result = await RetryPolicy(attempts=3, delays=(1, 2)).run(
        action,
        accept=lambda accepted: accepted,
    )
    assert result is True
    assert attempts == 3
    assert sleeps == [1, 2]


async def _record_sleep(sleeps: list[float], delay: float) -> None:
    sleeps.append(delay)


@pytest.mark.asyncio
async def test_retry_policy_retries_declared_exception_only(monkeypatch) -> None:
    monkeypatch.setattr("asyncio.sleep", lambda delay: _record_sleep([], delay))
    attempts = 0

    async def action() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError("transient")
        return "ready"

    assert (
        await RetryPolicy(attempts=3, delays=(0, 0)).run(
            action,
            accept=lambda value: value == "ready",
            retry_exceptions=(TimeoutError,),
        )
        == "ready"
    )
    assert attempts == 3


def test_redactor_covers_secret_corpus_and_preserves_operation_id() -> None:
    explicit = "model-secret-sentinel"
    operation_id = str(uuid4())
    text = (
        f"operation={operation_id} api_key={explicit} "
        "Authorization: Bearer bearer-secret-sentinel "
        'password="matrix-password-sentinel" '
        "token=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
    )
    redacted = BootstrapRedactor([explicit]).redact(text)
    assert operation_id in redacted
    for sentinel in (
        explicit,
        "bearer-secret-sentinel",
        "matrix-password-sentinel",
        "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
    ):
        assert sentinel not in redacted
    assert redacted.count("[REDACTED]") >= 4


def test_redactor_bounds_diagnostic_length() -> None:
    assert len(BootstrapRedactor(limit=40).redact("ordinary diagnostic " * 20)) == 40
