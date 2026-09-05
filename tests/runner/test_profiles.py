"""What a Runner advertises when it leases: the profiles its host can actually launch."""

from repomesh_runner.profiles import PROFILES, launchable_profiles


def test_launchable_profiles_are_those_with_a_resolvable_binary() -> None:
    present = {
        "repomesh-mock-agent": "/usr/local/bin/repomesh-mock-agent",
        "codex": "/usr/bin/codex",
    }

    def resolver(names: tuple[str, ...]) -> str | None:
        return next((present[name] for name in names if name in present), None)

    advertised = launchable_profiles(resolver)

    assert "mock" in advertised
    assert "codex" in advertised
    assert "claude-code" not in advertised
    assert all(profile.launchable for profile in PROFILES if profile.id in advertised)


def test_a_host_with_no_binaries_advertises_nothing() -> None:
    assert launchable_profiles(lambda names: None) == ()
