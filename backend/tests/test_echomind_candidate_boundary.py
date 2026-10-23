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

    def test_active_application_only_has_explicit_lazy_candidate_and_mcp_admission(self) -> None:
        paths = [BACKEND / "main.py"]
        for name in ("api", "agents", "core", "tooling", "memory", "monitor", "evaluation"):
            paths.extend((BACKEND / name).rglob("*.py"))
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            if path == BACKEND / "api/assistant.py":
                guards = [item.value for item in ast.walk(tree) if isinstance(item, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "travel_runtime" for target in item.targets)]
                expected = ast.parse('settings.assistant_runtime == "echomind_travel_v1"', mode="eval").body
                self.assertEqual(len(guards), 1)
                self.assertEqual(ast.dump(guards[0]), ast.dump(expected))
            for node in ast.walk(tree):
                names = [item.name for item in node.names] if isinstance(node, ast.Import) else (
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                )
                if any(name.split(".")[0] == "echomind_core" for name in names):
                    if path == BACKEND / "tooling/providers/amap_mcp.py":
                        self.assertIsInstance(node, ast.ImportFrom)
                        expected_imports = {
                            "echomind_core.diagnostics": ["quarantine_upstream_logging"],
                            "echomind_core.upstream.tooling.mcp_client": ["MCPServerConfig", "StandardMCPClient", "tool_alias"],
                        }
                        self.assertIn(node.module, expected_imports)
                        self.assertEqual([item.name for item in node.names], expected_imports[node.module])
                        continue
                    self.assertEqual(path, BACKEND / "api/assistant.py")
                    self.assertIsInstance(node, ast.ImportFrom)
                    self.assertEqual(node.module, "echomind_core.integration")
                    self.assertEqual([item.name for item in node.names], ["AdmittedTravelTurn"])
                    ancestors, parent = [], parents.get(node)
                    while parent is not None:
                        ancestors.append(parent)
                        parent = parents.get(parent)
                    self.assertTrue(any(isinstance(item, ast.If) and isinstance(item.test, ast.Name)
                                        and item.test.id == "travel_runtime" for item in ancestors))
                    self.assertTrue(any(isinstance(item, ast.AsyncFunctionDef) and item.name == "assistant_turn"
                                        for item in ancestors))
                self.assertFalse(any(name.split(".")[0] == "anthropic" for name in names),
                                 path.relative_to(BACKEND).as_posix())

    def test_mcp_provider_is_only_lazily_imported_after_server_rest_selection(self) -> None:
        source = ast.parse((BACKEND / "tooling/providers/chat_places_factory.py").read_text())
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                        and node.name == "create_chat_places_client")
        branches = [node for node in function.body if isinstance(node, ast.If)]
        rest = next(node for node in branches if isinstance(node.test, ast.Compare))
        expected = ast.parse('settings.chat_places_transport == "rest"', mode="eval").body
        self.assertEqual(ast.dump(rest.test), ast.dump(expected))
        self.assertIsInstance(rest.body[0], ast.Return)
        self.assertEqual(rest.body[0].value.func.id, "ChatPlacesClient")
        imports = [node for node in ast.walk(source) if isinstance(node, ast.ImportFrom)
                   and node.module == "tooling.providers.amap_mcp"]
        self.assertEqual(len(imports), 1)
        self.assertEqual([node.name for node in imports[0].names], ["ChatPlacesMCPClient"])
        self.assertTrue(any(isinstance(node, ast.Try) and imports[0] in node.body for node in function.body))
        self.assertGreater(imports[0].lineno, rest.end_lineno)

    def test_default_application_import_does_not_load_candidate_optional_services(self) -> None:
        script = (
            "import sys,json; sys.path.insert(0,sys.argv[1]); import main; "
            "print(json.dumps([x for x in sys.modules if x.split('.')[0] in "
            "('echomind_core','chromadb','redis','mcp','anthropic','jsonschema')]))"
        )
        result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(BACKEND)],
                                check=True, capture_output=True, text=True, timeout=15)
        self.assertEqual(json.loads(result.stdout), [])

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
