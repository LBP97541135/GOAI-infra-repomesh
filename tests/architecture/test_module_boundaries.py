import ast
import tomllib
from pathlib import Path

MODULE_ROOT = Path("src/repomesh/modules")
FORBIDDEN_DOMAIN_ROOTS = {
    "fastapi",
    "httpx",
    "pydantic_settings",
    "sqlalchemy",
    "repomesh.bootstrap",
    "repomesh.integrations",
}


def python_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def module_directories() -> list[Path]:
    return sorted(path.parent for path in MODULE_ROOT.glob("*/module.toml"))


def test_every_module_has_machine_readable_ownership() -> None:
    modules = module_directories()
    assert modules, "No business modules registered"
    for module in modules:
        manifest = tomllib.loads((module / "module.toml").read_text(encoding="utf-8"))
        assert manifest["name"] == module.name
        assert manifest["owner"]
        assert manifest["schema"]
        assert manifest["responsibilities"]
        assert (module / "README.md").is_file()


def test_domain_code_is_vendor_independent() -> None:
    violations: list[str] = []
    for path in MODULE_ROOT.glob("*/domain/**/*.py"):
        for imported in python_imports(path):
            if any(
                imported == root or imported.startswith(f"{root}.")
                for root in FORBIDDEN_DOMAIN_ROOTS
            ):
                violations.append(f"{path}: {imported}")
    assert not violations, "Domain dependency violations:\n" + "\n".join(violations)


def test_cross_module_imports_use_public_contracts() -> None:
    violations: list[str] = []
    for path in MODULE_ROOT.glob("*/**/*.py"):
        current_module = path.relative_to(MODULE_ROOT).parts[0]
        for imported in python_imports(path):
            prefix = "repomesh.modules."
            if not imported.startswith(prefix):
                continue
            parts = imported.removeprefix(prefix).split(".")
            target_module = parts[0]
            if target_module == current_module:
                continue
            if len(parts) < 2 or parts[1] != "contracts":
                violations.append(f"{path}: {imported}")
    assert not violations, "Cross-module imports must use contracts:\n" + "\n".join(violations)


def test_business_modules_do_not_depend_on_bootstrap_or_integrations() -> None:
    violations: list[str] = []
    for path in MODULE_ROOT.glob("*/**/*.py"):
        for imported in python_imports(path):
            if imported.startswith(("repomesh.bootstrap", "repomesh.integrations")):
                violations.append(f"{path}: {imported}")
    assert not violations, "Composition-root dependency violations:\n" + "\n".join(violations)
