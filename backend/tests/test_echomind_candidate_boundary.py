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
                expected = ast.parse('settings.assistant_runtime in {"echomind_travel_v1", "echomind_travel_full_v2"}', mode="eval").body
                self.assertEqual(len(guards), 1)
                self.assertEqual(ast.dump(guards[0]), ast.dump(expected))
                full_guards = [item.value for item in ast.walk(tree) if isinstance(item, ast.Assign)
                               and any(isinstance(target, ast.Name) and target.id == "full_runtime" for target in item.targets)]
                self.assertEqual(len(full_guards), 1)
                self.assertEqual(ast.dump(full_guards[0]), ast.dump(ast.parse(
                    'settings.assistant_runtime == "echomind_travel_full_v2"', mode="eval").body))
                # Full admission requires the new finite browser surface and
                # binding; registering an enum is not permission to bypass it.
                full_surface_guard = ast.parse('full_runtime and (not full_surface or command.dialogue_context is None)', mode="eval").body
                self.assertTrue(any(isinstance(item, ast.If) and ast.dump(item.test) == ast.dump(full_surface_guard)
                                    and isinstance(item.body[0], ast.Raise) for item in ast.walk(tree)))
                metadata = next(item for item in tree.body if isinstance(item, ast.FunctionDef)
                                and item.name == "_captured_skill_views")
                # Lazy full-only metadata imports do not execute a candidate.
                # Keep a fixed early guard rather than permitting arbitrary
                # echomind_core imports anywhere inside any helper function.
                metadata_guard = metadata.body[1]
                self.assertIsInstance(metadata_guard, ast.If)
                self.assertEqual(ast.dump(metadata_guard.test), ast.dump(ast.parse(
                    'runtime.get("runtime") != "echomind_travel_full_v2"', mode="eval").body))
                self.assertIsInstance(metadata_guard.body[0], ast.Return)
                metadata_calls = [item for item in ast.walk(tree) if isinstance(item, ast.Call)
                                  and isinstance(item.func, ast.Name) and item.func.id == "_captured_skill_views"]
                self.assertEqual(len(metadata_calls), 1)
                ancestors, parent = [], parents.get(metadata_calls[0])
                while parent is not None:
                    ancestors.append(parent)
                    parent = parents.get(parent)
                self.assertTrue(any(isinstance(item, ast.If) and isinstance(item.test, ast.Name)
                                    and item.test.id == "full_surface" for item in ancestors))
                self.assertTrue(any(isinstance(item, ast.AsyncFunctionDef) and item.name == "assistant_state"
                                    for item in ancestors))
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
                    ancestors, parent = [], parents.get(node)
                    while parent is not None:
                        ancestors.append(parent)
                        parent = parents.get(parent)
                    metadata_imports = {
                        "echomind_core.travel_domain": ["TravelIntent", "TravelRole"],
                        "echomind_core.travel_package": ["PROMPT_VERSION", "STAGE_PROMPTS", "VERSION"],
                        "echomind_core.travel_package_skills": ["SKILL_ROLES", "SKILL_VERSION"],
                        "echomind_core.upstream.agents.agent_orchestrator": ["AgentProfile"],
                        "echomind_core.provider": ["MODEL"],
                    }
                    if any(isinstance(item, ast.FunctionDef) and item.name == "_captured_skill_views"
                           for item in ancestors):
                        self.assertIn(node.module, metadata_imports)
                        self.assertEqual([item.name for item in node.names], metadata_imports[node.module])
                        self.assertTrue(all(item.asname is None for item in node.names))
                        continue
                    admission_imports = {
                        "echomind_core.integration": [("AdmittedTravelTurn", None)],
                        "echomind_core.integration_full": [("AdmittedFullTravelTurn", "AdmittedTravelTurn")],
                    }
                    self.assertIn(node.module, admission_imports)
                    self.assertEqual([(item.name, item.asname) for item in node.names], admission_imports[node.module])
                    self.assertTrue(any(isinstance(item, ast.If) and isinstance(item.test, ast.Name)
                                        and item.test.id == "travel_runtime" for item in ancestors))
                    self.assertTrue(any(isinstance(item, ast.AsyncFunctionDef) and item.name == "assistant_turn"
                                        for item in ancestors))
                    branch = next(item for item in ancestors if isinstance(item, ast.If)
                                  and isinstance(item.test, ast.Name) and item.test.id == "full_runtime")
                    expected_branch = branch.body if node.module == "echomind_core.integration_full" else branch.orelse
                    self.assertIn(node, expected_branch)
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
