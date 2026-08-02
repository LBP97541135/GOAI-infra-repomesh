import ast
from pathlib import Path

RUNNER_ROOT = Path("src/repomesh_runner")
MODULE_ROOT = Path("src/repomesh/modules")


def python_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_runner_does_not_import_product_control_plane() -> None:
    violations = [
        f"{path}: {imported}"
        for path in RUNNER_ROOT.rglob("*.py")
        for imported in python_imports(path)
        if imported == "repomesh" or imported.startswith("repomesh.")
    ]

    assert not violations, "Runner must remain independent from product modules:\n" + "\n".join(
        violations
    )


def test_business_modules_do_not_import_runner() -> None:
    violations = [
        f"{path}: {imported}"
        for path in MODULE_ROOT.glob("*/**/*.py")
        for imported in python_imports(path)
        if imported == "repomesh_runner" or imported.startswith("repomesh_runner.")
    ]

    assert not violations, "Business modules must use public contracts, not Runner:\n" + "\n".join(
        violations
    )
