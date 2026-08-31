from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _compose() -> dict:
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def test_only_bootstrap_mounts_docker_socket() -> None:
    services = _compose()["services"]
    socket_mount = "/var/run/docker.sock:/var/run/docker.sock"
    assert socket_mount in services["bootstrap"]["volumes"]
    for service_name in ("api", "web", "console-api", "console-web"):
        assert socket_mount not in services[service_name].get("volumes", [])


def test_bootstrap_has_no_port_and_uses_production_reconciler() -> None:
    bootstrap = _compose()["services"]["bootstrap"]
    assert "ports" not in bootstrap
    assert bootstrap["environment"]["REPOMESH_BOOTSTRAP_MODE"].endswith(":-production}")
    assert bootstrap["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert bootstrap["depends_on"]["api"]["condition"] == "service_healthy"


def test_api_secret_mount_is_read_only_and_bootstrap_is_writer() -> None:
    services = _compose()["services"]
    assert any(volume.endswith("}:/app/.secrets:ro") for volume in services["api"]["volumes"])
    assert any(
        volume.endswith("}:/app/.secrets") for volume in services["bootstrap"]["volumes"]
    )


def test_product_launchers_generate_fernet_key_before_compose() -> None:
    powershell = (ROOT / "scripts" / "start-platform.ps1").read_text(encoding="utf-8")
    shell = (ROOT / "scripts" / "start-platform.sh").read_text(encoding="utf-8")
    assert "New-FernetKey" in powershell
    assert "platform-credentials.key" in powershell
    assert "openssl rand -base64 32" in shell
    assert "platform-credentials.key" in shell


def test_bootstrap_image_copies_pinned_installer_at_build_time() -> None:
    dockerfile = (ROOT / "Dockerfile.bootstrap").read_text(encoding="utf-8")
    assert "COPY components/agentteams/install" in dockerfile
    assert 'CMD ["python", "-m", "repomesh.bootstrap_worker"]' in dockerfile
    assert "EXPOSE" not in dockerfile
