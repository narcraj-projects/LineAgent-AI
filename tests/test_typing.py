"""Check the scaffold's production functions all declare parameter/return types."""

import ast
from pathlib import Path


def test_production_functions_declare_type_hints() -> None:
    root = Path(__file__).resolve().parents[1]
    missing: list[str] = []
    paths = [root / "app.py"]
    for folder in ("src", "scripts", "evals"):
        paths.extend(sorted((root / folder).rglob("*.py")))
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.returns is None:
                missing.append(f"{path.name}:{node.lineno} return")
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            args += [arg for arg in (node.args.vararg, node.args.kwarg) if arg]
            for arg in args:
                if arg.arg not in {"self", "cls"} and arg.annotation is None:
                    missing.append(f"{path.name}:{node.lineno} {arg.arg}")
    assert not missing, "Missing type hints: " + ", ".join(missing)
