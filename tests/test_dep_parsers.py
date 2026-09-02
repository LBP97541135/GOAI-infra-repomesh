"""Tests for the structured build-manifest parsers (mechanism ① BUILD).

Covers the per-ecosystem parsers in ``application/dep_parsers.py`` and the
contract scan_remote relies on: identity (self-declared identifiers),
deps (direct build dependencies → BUILD evidence) and managed (Maven
version policy, never edges). Malformed content must yield an empty result,
never raise.
"""

import pytest

from repomesh.modules.repository_intelligence.application.dep_parsers import (
    parse_build_file,
    parse_cargo,
    parse_go_mod,
    parse_gradle,
    parse_package_json,
    parse_pom,
    parse_pyproject,
    parse_requirements,
)

# ---------------------------------------------------------------------------
# pom.xml
# ---------------------------------------------------------------------------


class TestParsePom:
    def test_direct_dependencies_and_identity(self) -> None:
        content = """
        <project xmlns="http://maven.apache.org/POM/4.0.0">
          <groupId>com.example</groupId>
          <artifactId>auth-service</artifactId>
          <dependencies>
            <dependency>
              <groupId>org.springframework</groupId>
              <artifactId>spring-core</artifactId>
              <version>6.1.0</version>
            </dependency>
            <dependency>
              <artifactId>no-group-dep</artifactId>
              <version>1.0</version>
            </dependency>
          </dependencies>
        </project>
        """
        result = parse_pom(content)

        assert result.identity == "com.example:auth-service"
        names = {d.coordinates for d in result.deps}
        assert names == {"org.springframework:spring-core", "no-group-dep"}
        assert all(not d.managed for d in result.deps)

    def test_dependency_management_is_classified_separately(self) -> None:
        """<dependencyManagement> is a version policy, not a dependency."""
        content = """
        <project>
          <groupId>com.example</groupId>
          <artifactId>parent</artifactId>
          <dependencies>
            <dependency><groupId>a</groupId><artifactId>direct-dep</artifactId></dependency>
          </dependencies>
          <dependencyManagement>
            <dependencies>
              <dependency>
                <groupId>org.lib</groupId>
                <artifactId>managed-lib</artifactId>
                <version>2.0</version>
              </dependency>
            </dependencies>
          </dependencyManagement>
        </project>
        """
        result = parse_pom(content)

        assert {d.coordinates for d in result.deps} == {"a:direct-dep"}
        assert {d.coordinates for d in result.managed} == {"org.lib:managed-lib"}
        # Managed entries are flagged so scan_remote never turns them into edges.
        assert all(d.managed for d in result.managed)

    def test_namespace_agnostic(self) -> None:
        content = (
            "<project><modelVersion>4.0.0</modelVersion>"
            "<groupId>io.x</groupId><artifactId>svc</artifactId>"
            "<dependencies><dependency><groupId>g</groupId>"
            "<artifactId>lib</artifactId></dependency></dependencies>"
            "</project>"
        )
        result = parse_pom(content)
        assert result.identity == "io.x:svc"
        assert {d.coordinates for d in result.deps} == {"g:lib"}

    def test_malformed_xml_yields_empty_result(self) -> None:
        result = parse_pom("<project><dependencies></project>")
        assert result.identity is None
        assert result.deps == ()
        assert result.managed == ()

    def test_bare_artifactId_identity_when_group_inherited(self) -> None:
        content = (
            "<project><artifactId>child-svc</artifactId>"
            "<dependencies><dependency><artifactId>x</artifactId></dependency>"
            "</dependencies></project>"
        )
        result = parse_pom(content)
        assert result.identity == "child-svc"
        assert {d.coordinates for d in result.deps} == {"x"}


# ---------------------------------------------------------------------------
# package.json
# ---------------------------------------------------------------------------


class TestParsePackageJson:
    def test_name_identity_and_dependency_maps(self) -> None:
        content = (
            '{"name": "order-service", "dependencies": {"express": "^4.18", '
            '"stripe": "^12.0"}, "devDependencies": {"mocha": "10"}, '
            '"peerDependencies": {"react": "^18"}}'
        )
        result = parse_package_json(content)

        assert result.identity == "order-service"
        assert {d.name for d in result.deps} == {"express", "stripe", "mocha", "react"}
        assert {d.name: d.version for d in result.deps}["express"] == "^4.18"

    def test_no_name_yields_no_identity(self) -> None:
        result = parse_package_json('{"dependencies": {"lodash": "^4"}}')
        assert result.identity is None
        assert {d.name for d in result.deps} == {"lodash"}

    def test_invalid_json_yields_empty_result(self) -> None:
        assert parse_package_json("{not json").deps == ()
        assert parse_package_json("[1, 2, 3]").deps == ()  # not a dict

    def test_duplicate_across_sections_registered_once(self) -> None:
        content = (
            '{"name": "s", "dependencies": {"pkg": "^1"}, '
            '"devDependencies": {"pkg": "^2"}}'
        )
        result = parse_package_json(content)
        assert len(result.deps) == 1


# ---------------------------------------------------------------------------
# go.mod
# ---------------------------------------------------------------------------


class TestParseGoMod:
    def test_module_identity_and_require_block(self) -> None:
        content = """module github.com/acme/order-service

go 1.22

require (
\tgithub.com/gin-gonic/gin v1.9.1
\tgithub.com/stretchr/testify v1.8.4 // indirect
)

require github.com/google/uuid v1.6.0
"""
        result = parse_go_mod(content)

        assert result.identity == "github.com/acme/order-service"
        assert {d.name for d in result.deps} == {
            "github.com/gin-gonic/gin",
            "github.com/stretchr/testify",
            "github.com/google/uuid",
        }
        versions = {d.name: d.version for d in result.deps}
        assert versions["github.com/gin-gonic/gin"] == "v1.9.1"

    def test_replace_and_exclude_are_ignored(self) -> None:
        content = """module m

require github.com/a/b v1.0.0

replace github.com/a/b => github.com/fork/b v1.1.0

exclude github.com/c/d v0.5.0
"""
        result = parse_go_mod(content)
        assert {d.name for d in result.deps} == {"github.com/a/b"}

    def test_no_requires_yields_empty_deps(self) -> None:
        result = parse_go_mod("module example.com/empty\n\ngo 1.22\n")
        assert result.identity == "example.com/empty"
        assert result.deps == ()


# ---------------------------------------------------------------------------
# pyproject.toml / requirements.txt
# ---------------------------------------------------------------------------


class TestParsePyproject:
    def test_pep621_project_table(self) -> None:
        content = """[project]
name = "my-tool"
version = "1.0.0"
dependencies = [
    "fastapi>=0.100",
    "sqlalchemy[asyncio]>=2.0",
    "stripe==5.0.0; python_version >= '3.9'",
]
"""
        result = parse_pyproject(content)

        assert result.identity == "my-tool"
        assert {d.name for d in result.deps} == {"fastapi", "sqlalchemy", "stripe"}
        versions = {d.name: d.version for d in result.deps}
        assert versions["fastapi"] == ">=0.100"
        # Extras and environment markers strip back to the bare name.
        assert versions["sqlalchemy"] == "[asyncio]>=2.0"

    def test_poetry_table_falls_back_for_identity_and_deps(self) -> None:
        content = """[tool.poetry]
name = "poetry-app"
version = "0.1.0"

[tool.poetry.dependencies]
python = "^3.11"
requests = "^2.31"
click = { version = "^8.1", extras = ["color"] }
"""
        result = parse_pyproject(content)

        assert result.identity == "poetry-app"
        assert {d.name for d in result.deps} == {"requests", "click"}
        # The python constraint is not a dependency of the package.
        assert "python" not in {d.name for d in result.deps}
        versions = {d.name: d.version for d in result.deps}
        assert versions["requests"] == "^2.31"
        assert versions["click"] == "^8.1"

    def test_invalid_toml_yields_empty_result(self) -> None:
        result = parse_pyproject("[project\nname =")
        assert result.identity is None
        assert result.deps == ()


class TestParseRequirements:
    def test_pep508_lines(self) -> None:
        content = (
            "fastapi>=0.100\n"
            "sqlalchemy[asyncio]\n"
            "stripe==5.0.0 ; python_version >= '3.9'\n"
            "# comment\n"
            "-r base.txt\n"
            "\n"
        )
        result = parse_requirements(content)

        assert {d.name for d in result.deps} == {"fastapi", "sqlalchemy", "stripe"}
        versions = {d.name: d.version for d in result.deps}
        assert versions["fastapi"] == ">=0.100"
        assert versions["stripe"] == "==5.0.0"
        # No identity: a requirements file says nothing about who this repo is.
        assert result.identity is None


# ---------------------------------------------------------------------------
# parse_build_file dispatch
# ---------------------------------------------------------------------------


class TestParseBuildFileDispatch:
    def test_known_files_dispatch_to_their_parser(self) -> None:
        assert parse_build_file("pom.xml", "<project/>") is not None
        assert parse_build_file("package.json", "{}") is not None
        assert parse_build_file("go.mod", "module m") is not None
        assert parse_build_file("pyproject.toml", "") is not None
        assert parse_build_file("requirements.txt", "") is not None
        assert parse_build_file("build.gradle", "") is not None
        assert parse_build_file("build.gradle.kts", "") is not None
        assert parse_build_file("Cargo.toml", "") is not None

    @pytest.mark.parametrize(
        "filename",
        ["setup.py", "Gemfile", "package-lock.json", "gradle.properties"],
    )
    def test_unknown_files_return_none(self, filename: str) -> None:
        """Files we have no parser for contribute nothing rather than a guess."""
        assert parse_build_file(filename, "anything at all") is None


# ---------------------------------------------------------------------------
# build.gradle / build.gradle.kts
# ---------------------------------------------------------------------------


class TestParseGradle:
    def test_coordinate_style_dependencies(self) -> None:
        content = """
        plugins {
            id 'java'
        }
        dependencies {
            implementation 'org.springframework.boot:spring-boot-starter-web:3.2.0'
            implementation "com.example:order-client:2.1.0"
            api 'com.example:common:1.0.0'
            compileOnly 'org.projectlombok:lombok:1.18.30'
            testImplementation 'org.junit.jupiter:junit-jupiter:5.10.0'
        }
        """
        result = parse_gradle(content)

        names = {d.coordinates for d in result.deps}
        assert names == {
            "org.springframework.boot:spring-boot-starter-web",
            "com.example:order-client",
            "com.example:common",
            "org.projectlombok:lombok",
        }
        # testImplementation is a test-only configuration → never evidence.
        assert "org.junit.jupiter:junit-jupiter" not in names
        # No identity: the Gradle project name lives in settings.gradle.
        assert result.identity is None

    def test_named_form_and_multiline_declaration(self) -> None:
        content = """
        dependencies {
            implementation group: 'com.example', name: 'auth-client', version: '3.1.0'
            implementation(
                'com.example:reporting-client:1.2.0'
            )
        }
        """
        result = parse_gradle(content)

        names = {d.coordinates for d in result.deps}
        assert names == {
            "com.example:auth-client",
            "com.example:reporting-client",
        }

    def test_placeholders_and_local_declarations_are_skipped(self) -> None:
        content = """
        dependencies {
            implementation "com.example:dynamic:$version"
            implementation project(':shared')
            implementation files('libs/local.jar')
            implementation fileTree(dir: 'libs', include: '*.jar')
            implementation 'com.example:concrete:1.0.0'
        }
        """
        result = parse_gradle(content)

        assert [d.coordinates for d in result.deps] == ["com.example:concrete"]

    def test_malformed_content_yields_empty_result(self) -> None:
        result = parse_gradle("this is not gradle")
        assert result.identity is None
        assert result.deps == ()

    def test_kotlin_dsl_coordinates(self) -> None:
        content = """
        dependencies {
            implementation("com.example:kotlin-client:1.0.0")
            implementation("com.example:another:2.0.0") { exclude group: "x" }
        }
        """
        result = parse_gradle(content)

        names = {d.coordinates for d in result.deps}
        assert names == {"com.example:kotlin-client", "com.example:another"}


# ---------------------------------------------------------------------------
# Cargo.toml
# ---------------------------------------------------------------------------


class TestParseCargo:
    def test_plain_and_table_dependencies_with_identity(self) -> None:
        content = """
        [package]
        name = "order-service"
        version = "0.1.0"

        [dependencies]
        tokio = { version = "1.35", features = ["full"] }
        serde = "1.0"
        order-client = { path = "../order-client" }

        [dev-dependencies]
        criterion = "0.5"

        [build-dependencies]
        tonic-build = "0.11"
        """
        result = parse_cargo(content)

        assert result.identity == "order-service"
        names = {d.name for d in result.deps}
        assert names == {"tokio", "serde", "order-client", "tonic-build"}
        # dev-dependencies are test-only → never evidence.
        assert "criterion" not in names

    def test_workspace_dependencies_are_not_direct_deps(self) -> None:
        content = """
        [workspace.dependencies]
        anyhow = "1.0"

        [package]
        name = "app"

        [dependencies]
        anyhow = { workspace = true }
        """
        result = parse_cargo(content)

        # ``workspace = true`` inherits a version policy — no concrete
        # coordinate is declared here, and [workspace.dependencies] is a
        # version table, never a direct dependency.
        assert result.deps == ()

    def test_malformed_content_yields_empty_result(self) -> None:
        result = parse_cargo("not [valid toml")
        assert result.identity is None
        assert result.deps == ()
