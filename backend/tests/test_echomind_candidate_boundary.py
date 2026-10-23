"""Stage-1 isolation is executable, not a production enablement flag."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
import tomllib
import unittest


BACKEND = Path(__file__).resolve().parents[1]


class CandidateBoundaryTests(unittest.TestCase):
    def test_package_import_is_lazy_without_optional_dependencies_or_service_start(self) -> None:
        script = (
            "import sys,json; sys.path.insert(0,sys.argv[1]); import echomind_core; "
            "print(json.dumps({'enabled':echomind_core.PRODUCT_ENABLED,'loaded':"
            "[x for x in sys.modules if x.split('.')[0] in "
            "('chromadb','redis','mcp','anthropic','httpx','jsonschema')]}))"
        )
        result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(BACKEND)],
                                check=True, capture_output=True, text=True, timeout=10)
        self.assertEqual(json.loads(result.stdout), {"enabled": False, "loaded": []})

    def test_active_application_does_not_import_candidate_or_anthropic(self) -> None:
        paths = [BACKEND / "main.py"]
        for name in ("api", "agents", "core", "tooling", "memory", "monitor", "evaluation"):
            paths.extend((BACKEND / name).rglob("*.py"))
        for path in paths:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [item.name for item in node.names] if isinstance(node, ast.Import) else (
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                )
                self.assertFalse(any(name.split(".")[0] in {"echomind_core", "anthropic"} for name in names),
                                 path.relative_to(BACKEND).as_posix())

    def test_service_dependencies_are_opt_in_and_original_sdk_is_not_installed(self) -> None:
        data = tomllib.loads((BACKEND / "pyproject.toml").read_text())
        direct = data["project"]["dependencies"]
        optional = data["project"]["optional-dependencies"]["echomind"]
        for name in ("chromadb", "redis", "mcp", "prometheus-client", "jsonschema"):
            self.assertFalse(any(value.startswith(name + "=") for value in direct))
            self.assertTrue(any(value.startswith(name + "==") for value in optional))
        self.assertFalse(any("anthropic" in value for value in direct + optional))

    def test_production_dependency_versions_are_unchanged_by_optional_extra(self) -> None:
        # A baseline captured as ordinary data, not dependent on a local Git ref.
        baseline = json.loads((BACKEND / "echomind_core" / "application-dependencies.json").read_text())
        locked = tomllib.loads((BACKEND / "uv.lock").read_text())
        current = {item["name"]: item["version"] for item in locked["package"]}
        self.assertTrue(baseline)
        self.assertEqual({name: current.get(name) for name in baseline}, baseline)


if __name__ == "__main__":
    unittest.main()
