"""Isolated stdlib policy checks; no ONNX/Chroma import or real transport.

Each child loads this one file by spec, bypassing candidate package startup so
the tests can exercise a genuinely first configuration without parent-process
environment/module leakage or depending on optional dependencies being present.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

POLICY = Path(__file__).resolve().parents[1] / "echomind_core" / "native_runtime.py"
PRELUDE = """
import importlib.abc
import importlib.util
import os
import sys
from types import ModuleType
from concurrent.futures import ThreadPoolExecutor

class RejectOptionalImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.', 1)[0] in {'onnxruntime', 'chromadb', 'redis', 'httpx', 'echomind_core'}:
            raise AssertionError('unexpected optional import')
        return None

sys.meta_path.insert(0, RejectOptionalImports())
spec = importlib.util.spec_from_file_location('isolated_native_runtime', sys.argv[1])
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

def rejected(expected):
    try:
        policy.configure_native_runtime()
    except policy.NativeRuntimeError as error:
        assert str(error) == expected
        assert error.code == expected
    else:
        raise AssertionError('expected bounded policy refusal')
"""


class NativeRuntimePolicyTests(unittest.TestCase):
    def child(self, code: str) -> None:
        environment = dict(os.environ)
        # This only alters the disposable child environment, never the runner,
        # a real configuration file, or an existing service process.
        environment.pop("ORT_DISABLE_TELEMETRY", None)
        result = subprocess.run(
            [sys.executable, "-B", "-c", PRELUDE + "\n" + textwrap.dedent(code), str(POLICY)],
            env=environment, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_missing_optional_dependencies_need_not_be_imported(self):
        self.child("""
            policy.configure_native_runtime()
            assert os.environ['ORT_DISABLE_TELEMETRY'] == '1'
            assert not any(name.split('.', 1)[0] in {'onnxruntime', 'chromadb', 'redis', 'httpx', 'echomind_core'}
                           for name in sys.modules)
        """)

    def test_existing_exact_opt_out_is_accepted_and_idempotent(self):
        self.child("""
            os.environ['ORT_DISABLE_TELEMETRY'] = '1'
            policy.configure_native_runtime()
            policy.configure_native_runtime()
            assert os.environ['ORT_DISABLE_TELEMETRY'] == '1'
        """)

    def test_explicit_conflicts_are_not_overwritten_or_logged(self):
        self.child("""
            for value in ['0', '', 'true', ' 1', '1 ', 'private-operator-value']:
                os.environ['ORT_DISABLE_TELEMETRY'] = value
                rejected('native_telemetry_policy_conflict')
                assert os.environ['ORT_DISABLE_TELEMETRY'] == value
            os.environ['ORT_DISABLE_TELEMETRY'] = '1'
            policy.configure_native_runtime()
        """)

    def test_first_configuration_refuses_loaded_root_or_child_even_if_none(self):
        self.child("""
            for name, value in [('onnxruntime', ModuleType('onnxruntime')),
                                ('onnxruntime.capi._pybind_state', ModuleType('native')),
                                ('onnxruntime.capi', None)]:
                sys.modules[name] = value
                rejected('native_runtime_already_initialized')
                assert 'ORT_DISABLE_TELEMETRY' not in os.environ
                del sys.modules[name]
            policy.configure_native_runtime()
        """)

    def test_existing_opt_out_cannot_claim_to_protect_already_loaded_native(self):
        self.child("""
            os.environ['ORT_DISABLE_TELEMETRY'] = '1'
            sys.modules['onnxruntime'] = ModuleType('onnxruntime')
            rejected('native_runtime_already_initialized')
        """)

    def test_unrelated_module_prefix_is_not_treated_as_native(self):
        self.child("""
            sys.modules['onnxruntime_other'] = ModuleType('other')
            policy.configure_native_runtime()
        """)

    def test_subsequent_calls_detect_removed_or_changed_policy_without_repair(self):
        self.child("""
            policy.configure_native_runtime()
            for value in [None, '0', '', 'private-changed-value']:
                if value is None:
                    del os.environ['ORT_DISABLE_TELEMETRY']
                else:
                    os.environ['ORT_DISABLE_TELEMETRY'] = value
                rejected('native_telemetry_policy_changed')
                assert os.environ.get('ORT_DISABLE_TELEMETRY') == value
                os.environ['ORT_DISABLE_TELEMETRY'] = '1'
                policy.configure_native_runtime()
        """)

    def test_serialized_concurrent_configuration_is_idempotent(self):
        self.child("""
            with ThreadPoolExecutor(max_workers=16) as pool:
                assert list(pool.map(lambda _: policy.configure_native_runtime(), range(64))) == [None] * 64
            assert os.environ['ORT_DISABLE_TELEMETRY'] == '1'
        """)

    def test_policy_precedes_optional_initialization_and_remains_valid_afterwards(self):
        self.child("""
            events = []
            policy.configure_native_runtime()
            events.append(('policy', os.environ['ORT_DISABLE_TELEMETRY']))
            # A controlled stand-in records what a later import would observe;
            # do not initialize the real dependency in a policy unit test.
            assert os.environ['ORT_DISABLE_TELEMETRY'] == '1'
            sys.modules['onnxruntime'] = ModuleType('onnxruntime')
            events.append(('controlled-native-init', os.environ['ORT_DISABLE_TELEMETRY']))
            policy.configure_native_runtime()
            assert events == [('policy', '1'), ('controlled-native-init', '1')]
        """)

    def test_environment_write_failure_has_only_a_bounded_error(self):
        self.child("""
            class BrokenEnvironment(dict):
                def __setitem__(self, key, value):
                    raise OSError('private low-level environment detail')
            previous = os.environ
            os.environ = BrokenEnvironment()
            rejected('native_telemetry_policy_unavailable')
            os.environ = previous
            policy.configure_native_runtime()
        """)

    def test_environment_write_not_retained_is_not_marked_configured(self):
        self.child("""
            class IgnoredEnvironment(dict):
                def __setitem__(self, key, value):
                    pass
            previous = os.environ
            os.environ = IgnoredEnvironment()
            rejected('native_telemetry_policy_unavailable')
            os.environ = previous
            policy.configure_native_runtime()
        """)

    def test_exception_constructor_cannot_echo_arbitrary_details(self):
        self.child("""
            error = policy.NativeRuntimeError('private arbitrary exception detail')
            assert str(error) == 'native_telemetry_policy_unavailable'
            assert error.code == 'native_telemetry_policy_unavailable'
        """)


if __name__ == "__main__":
    unittest.main()
