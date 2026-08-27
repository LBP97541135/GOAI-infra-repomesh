"""Tests for the cross-repository source-reference parsers (mechanism ⑤).

Covers ``.gitmodules`` (submodule URLs), ``go.work`` (``use``/``replace``),
``package.json`` (``workspaces``) and ``Cargo.toml`` (workspace members /
``path`` deps) parsing in ``application/source_refs.py``. The contract is
strict: only references that *cross the repository boundary* (a ``../``
path, a submodule URL) are refs — in-repo entries (``use ./cmd``,
``packages/*``) are this repository's own code and must contribute
nothing. Placeholders and malformed files must contribute nothing either.
"""

from repomesh.modules.repository_intelligence.application.source_refs import (
    SourceParseResult,
    SourceRef,
    parse_source_ref_file,
)


def _refs(filename: str, content: str) -> tuple[str, ...]:
    return tuple(r.name for r in parse_source_ref_file(filename, content).refs)


# ---------------------------------------------------------------------------
# .gitmodules — submodule URLs
# ---------------------------------------------------------------------------


class TestGitmodules:
    def test_https_url_yields_repo_name(self) -> None:
        content = (
            '[submodule "ts-common"]\n'
            "\tpath = ts-common\n"
            "\turl = https://github.com/acme/ts-common.git\n"
        )
        assert _refs(".gitmodules", content) == ("ts-common",)

    def test_scp_style_url_yields_repo_name(self) -> None:
        content = (
            '[submodule "util"]\n'
            "\tpath = vendored/util\n"
            "\turl = git@github.com:acme/ts-util.git\n"
        )
        assert _refs(".gitmodules", content) == ("ts-util",)

    def test_ssh_url_without_dotgit(self) -> None:
        content = (
            '[submodule "lib"]\n'
            "\tpath = lib\n"
            "\turl = ssh://git@host.acme/team/ts-lib\n"
        )
        assert _refs(".gitmodules", content) == ("ts-lib",)

    def test_multiple_submodules_aggregate(self) -> None:
        content = (
            '[submodule "ts-common"]\n'
            "\tpath = ts-common\n"
            "\turl = https://github.com/acme/ts-common.git\n"
            '[submodule "ts-frontend"]\n'
            "\tpath = web/ts-frontend\n"
            "\turl = git@github.com:acme/ts-frontend.git\n"
        )
        assert _refs(".gitmodules", content) == ("ts-common", "ts-frontend")

    def test_duplicate_urls_deduplicated(self) -> None:
        content = (
            '[submodule "a"]\n'
            "\turl = https://github.com/acme/ts-common.git\n"
            '[submodule "b"]\n'
            "\turl = https://github.com/acme/TS-COMMON.git\n"
        )
        assert _refs(".gitmodules", content) == ("ts-common",)

    def test_section_without_url_contributes_nothing(self) -> None:
        content = '[submodule "docs"]\n\tpath = docs\n'
        assert _refs(".gitmodules", content) == ()

    def test_placeholder_url_is_skipped(self) -> None:
        content = (
            '[submodule "shared"]\n'
            "\tpath = shared\n"
            "\turl = https://github.com/${ORG}/ts-shared.git\n"
        )
        assert _refs(".gitmodules", content) == ()

    def test_malformed_content_yields_empty(self) -> None:
        assert _refs(".gitmodules", "[unterminated\n\turl = broken") == ()
        assert _refs(".gitmodules", "") == ()


# ---------------------------------------------------------------------------
# go.work — use / replace
# ---------------------------------------------------------------------------


class TestGoWork:
    def test_use_block_outside_path_yields_ref(self) -> None:
        content = (
            "go 1.22.0\n\n"
            "use (\n"
            "\t./cmd/app\n"
            "\t../ts-common\n"
            ")\n"
        )
        assert _refs("go.work", content) == ("ts-common",)

    def test_use_inline_outside_path_yields_ref(self) -> None:
        content = "go 1.22.0\nuse ./tool ../ts-common\n"
        assert _refs("go.work", content) == ("ts-common",)

    def test_in_repo_use_contributes_nothing(self) -> None:
        content = "go 1.22.0\nuse (\n\t./cmd\n\t./internal/util\n)\n"
        assert _refs("go.work", content) == ()

    def test_replace_outside_path_yields_ref(self) -> None:
        content = "go 1.22.0\nreplace example.com/legacy => ../ts-legacy\n"
        assert _refs("go.work", content) == ("ts-legacy",)

    def test_replace_block_outside_paths(self) -> None:
        content = (
            "go 1.22.0\n"
            "replace (\n"
            "\texample.com/one => ../ts-one\n"
            "\texample.com/two => ../shared/ts-two\n"
            ")\n"
        )
        assert _refs("go.work", content) == ("ts-one", "ts-two")

    def test_replace_remote_module_is_not_a_source_ref(self) -> None:
        content = "replace example.com/old => example.com/new v1.2.3\n"
        assert _refs("go.work", content) == ()

    def test_malformed_content_yields_empty(self) -> None:
        assert _refs("go.work", "") == ()


# ---------------------------------------------------------------------------
# package.json workspaces
# ---------------------------------------------------------------------------


class TestPackageWorkspaces:
    def test_outside_glob_yields_ref(self) -> None:
        content = '{"name": "mono", "workspaces": ["packages/*", "../shared/*"]}'
        assert _refs("package.json", content) == ("shared",)

    def test_in_repo_globs_contribute_nothing(self) -> None:
        content = '{"workspaces": ["packages/*", "apps/*", "libs/**"]}'
        assert _refs("package.json", content) == ()

    def test_packages_dict_form(self) -> None:
        content = (
            '{"workspaces": {"packages": ["apps/*", "../ts-util"], '
            '"nohoist": ["**/react"]}}'
        )
        assert _refs("package.json", content) == ("ts-util",)

    def test_multilevel_outside_path_takes_terminal_segment(self) -> None:
        content = '{"workspaces": ["../../org/libs/ts-common"]}'
        assert _refs("package.json", content) == ("ts-common",)

    def test_malformed_json_yields_empty(self) -> None:
        assert _refs("package.json", "{not json") == ()
        assert _refs("package.json", "") == ()


# ---------------------------------------------------------------------------
# Cargo.toml — workspace members and path dependencies
# ---------------------------------------------------------------------------


class TestCargoWorkspace:
    def test_outside_member_yields_ref(self) -> None:
        content = (
            '[workspace]\n'
            'members = ["crates/*", "../shared-crate"]\n'
        )
        assert _refs("Cargo.toml", content) == ("shared-crate",)

    def test_in_repo_members_contribute_nothing(self) -> None:
        content = '[workspace]\nmembers = ["crates/*", "apps/engine"]\n'
        assert _refs("Cargo.toml", content) == ()

    def test_path_dependency_outside_yields_ref(self) -> None:
        content = (
            '[dependencies]\n'
            'util = { path = "../ts-util" }\n'
        )
        assert _refs("Cargo.toml", content) == ("ts-util",)

    def test_workspace_path_dependency_yields_ref(self) -> None:
        content = (
            '[workspace]\n'
            'members = ["crates/*"]\n\n'
            '[workspace.dependencies]\n'
            'shared = { path = "../ts-shared" }\n'
        )
        assert _refs("Cargo.toml", content) == ("ts-shared",)

    def test_registry_and_crates_deps_contribute_nothing(self) -> None:
        content = (
            '[dependencies]\n'
            'serde = "1.0"\n'
            'local = { path = "crates/local" }\n'
        )
        assert _refs("Cargo.toml", content) == ()

    def test_malformed_toml_yields_empty(self) -> None:
        assert _refs("Cargo.toml", "[workspace\nmembers = [") == ()
        assert _refs("Cargo.toml", "") == ()


# ---------------------------------------------------------------------------
# Dispatch and contract
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_unknown_file_contributes_nothing(self) -> None:
        assert _refs("README.md", "anything") == ()
        assert _refs("ci/pipeline.yaml", "anything") == ()

    def test_basename_routing_regardless_of_directory(self) -> None:
        nested = (
            '[submodule "ts-common"]\n'
            "\turl = https://github.com/acme/ts-common.git\n"
        )
        assert _refs("vendor/.gitmodules", nested) == ("ts-common",)
        assert _refs("submodules/.gitmodules", nested) == ("ts-common",)

    def test_source_ref_contract_is_fixed(self) -> None:
        """SOURCE refs are always confirmed evidence, mechanism fixed."""
        content = (
            '[submodule "ts-common"]\n'
            "\turl = https://github.com/acme/ts-common.git\n"
        )
        parsed = parse_source_ref_file(".gitmodules", content)
        assert parsed.refs[0].mechanism == "SOURCE"
        assert parsed.refs[0].confidence == "confirmed"

    def test_empty_defaults(self) -> None:
        assert SourceParseResult().refs == ()
        ref = SourceRef(name="ts-common")
        assert ref.mechanism == "SOURCE"
        assert ref.confidence == "confirmed"
