"""Bounded *process-exit* checks for the optional EchoMind dependencies.

These are not semantic/model evaluations.  A macOS OS sandbox denies every IP
connection, including connections made by native code; Unix sockets remain
available only for the existing tests' private Redis.  The native deny rule is
checked before importing the candidate in every child.  No environment file,
user database, inference model, HTTP service or customer prompt is used.

An assertion summary or READY marker is insufficient: each child must actually
exit successfully.  A deadline/overflow causes failure, followed by process-
group cleanup; forced cleanup can never turn the result into a pass.  Linux
and other platforms are explicitly unverified by this macOS-specific check.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest


_BACKEND = Path(__file__).resolve().parents[1]
_SANDBOX = Path("/usr/bin/sandbox-exec")
_MAX_OUTPUT = 65_536
_RESULT_PREFIX = "LIFECYCLE_RESULT="

# The private storage tests use a Unix socket, not a TCP Redis port.  These
# fixed IP wildcard denials intentionally have no host/port allow exception.
# The ctypes probes below verify both address families before optional imports.
_PROFILE = """(version 1)
(allow default)
(deny network-outbound (remote ip "*:*"))
(deny network-inbound (local ip "*:*"))
(deny network-bind (local ip "*:*"))
"""

_GUARD = r"""
import ctypes
import errno
import json
import socket
import struct
import sys

libc = ctypes.CDLL(None, use_errno=True)
libc.socket.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
libc.socket.restype = ctypes.c_int
libc.connect.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
libc.connect.restype = ctypes.c_int
libc.close.argtypes = [ctypes.c_int]
libc.close.restype = ctypes.c_int
for family, address in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
    descriptor = libc.socket(family, socket.SOCK_STREAM, 0)
    assert descriptor >= 0, 'native_guard_socket_unavailable'
    try:
        if family == socket.AF_INET:
            packed = bytes((16, family)) + struct.pack('!H', 9)
            packed += socket.inet_pton(family, address) + bytes(8)
        else:
            packed = bytes((28, family)) + struct.pack('!H', 9) + bytes(4)
            packed += socket.inet_pton(family, address) + bytes(4)
        target = ctypes.create_string_buffer(packed)
        ctypes.set_errno(0)
        result = libc.connect(descriptor, target, len(packed))
        assert result == -1 and ctypes.get_errno() in (errno.EPERM, errno.EACCES), 'native_ip_guard_not_enforced'
    finally:
        libc.close(descriptor)
"""


@unittest.skipUnless(sys.platform == "darwin" and _SANDBOX.is_file(),
                     "requires macOS sandbox-exec; Linux lifecycle remains separately unverified")
class EchoMindLifecycleTests(unittest.TestCase):
    def _require(self, *names: str) -> None:
        missing = [name for name in names if importlib.util.find_spec(name) is None]
        if missing:
            self.skipTest("requires explicitly installed optional test dependencies: " + ", ".join(missing))

    @staticmethod
    def _stop_failed_group(process: subprocess.Popen[bytes]) -> None:
        """Reap only this test's group; callers already classified it failed."""
        for action, deadline in ((signal.SIGTERM, 2), (signal.SIGKILL, 2)):
            try:
                os.killpg(process.pid, action)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=deadline)
            except subprocess.TimeoutExpired:
                continue
            # A descendant might still own the child's inherited stdout.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return

    def _probe(self, body: str, *, deadline: float = 30) -> dict[str, object]:
        self.assertLessEqual(deadline, 55)
        script = _GUARD + "\n" + textwrap.dedent(body)
        with tempfile.TemporaryDirectory(prefix="trippilot-lifecycle-") as temporary:
            private = Path(temporary).resolve()
            os.chmod(private, 0o700)
            # Do not inherit credentials, proxy settings, operator configuration
            # or dotenv pointers.  HOME/CODEX_HOME are neither changed nor read.
            environment = {
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                "PYTHONPATH": str(_BACKEND),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "TMPDIR": str(private),
                "XDG_CACHE_HOME": str(private / "cache"),
                "ANONYMIZED_TELEMETRY": "False",
                "ECHOMIND_SEED_DEMO_DOCS": "false",
                "INTENT_EMBEDDING_PROVIDER": "local",
            }
            process = subprocess.Popen(
                [str(_SANDBOX), "-p", _PROFILE, sys.executable, "-B", "-c", script],
                cwd=_BACKEND, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
            )
            self.assertIsNotNone(process.stdout)
            captured = bytearray()
            overflow = threading.Event()
            finished = threading.Event()

            def read_bounded() -> None:
                try:
                    assert process.stdout is not None
                    while chunk := process.stdout.read(4096):
                        if len(captured) + len(chunk) > _MAX_OUTPUT:
                            overflow.set()
                        available = max(0, _MAX_OUTPUT - len(captured))
                        captured.extend(chunk[:available])
                finally:
                    finished.set()

            reader = threading.Thread(target=read_bounded, daemon=True)
            reader.start()
            expires = time.monotonic() + deadline
            failed = None
            while process.poll() is None:
                if overflow.is_set():
                    failed = "bounded_output_exceeded"
                    break
                if time.monotonic() >= expires:
                    failed = "process_exit_deadline"
                    break
                time.sleep(0.02)
            if failed is not None:
                self._stop_failed_group(process)
            else:
                process.wait(timeout=1)
            reader.join(timeout=1)
            if not finished.is_set():
                failed = failed or "descendant_or_output_handle_not_reaped"
                self._stop_failed_group(process)
                reader.join(timeout=1)
            assert process.stdout is not None
            process.stdout.close()
            digest = hashlib.sha256(captured).hexdigest()
            # Failure reporting is deliberately bounded and sanitized.  Never
            # echo a child traceback, exception input or arbitrary stdout.
            if failed is not None or overflow.is_set() or process.returncode != 0:
                self.fail(f"{failed or 'child_exit_failed'}; exit={process.returncode}; "
                          f"output_bytes={len(captured)}; output_sha256={digest}")
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                pass
            else:
                self._stop_failed_group(process)
                self.fail("descendant_process_remained_after_successful_exit")
            records = []
            for line in captured.decode("utf-8", errors="replace").splitlines():
                if line.startswith(_RESULT_PREFIX):
                    records.append(json.loads(line[len(_RESULT_PREFIX):]))
            self.assertEqual(len(records), 1, "expected one fixed result AND a clean process exit")
            self.assertIs(type(records[0]), dict)
            return records[0]

    def test_package_import_is_lightweight_and_exits_cleanly(self):
        result = self._probe("""
            import os
            import echomind_core
            assert os.environ['ORT_DISABLE_TELEMETRY'] == '1'
            assert not any(name == 'onnxruntime' or name.startswith('onnxruntime.') for name in sys.modules)
            assert not any(name == 'chromadb' or name.startswith('chromadb.') for name in sys.modules)
            print('LIFECYCLE_RESULT=' + json.dumps({'kind': 'package-import', 'native_loaded': False}))
        """)
        self.assertEqual(result, {"kind": "package-import", "native_loaded": False})

    def test_storage_import_has_preinit_policy_and_natural_exit(self):
        self._require("chromadb", "redis")
        result = self._probe("""
            import os
            import echomind_core.storage
            assert os.environ['ORT_DISABLE_TELEMETRY'] == '1'
            assert 'onnxruntime' in sys.modules
            print('LIFECYCLE_RESULT=' + json.dumps({'kind': 'storage-import', 'native_loaded': True}))
        """)
        self.assertEqual(result, {"kind": "storage-import", "native_loaded": True})

    def test_direct_http_integration_import_does_not_load_native_storage(self):
        self._require("jsonschema", "prometheus_client")
        result = self._probe("""
            import os
            import echomind_core.integration
            assert os.environ['ORT_DISABLE_TELEMETRY'] == '1'
            assert not any(name == 'onnxruntime' or name.startswith('onnxruntime.') for name in sys.modules)
            assert not any(name == 'chromadb' or name.startswith('chromadb.') for name in sys.modules)
            print('LIFECYCLE_RESULT=' + json.dumps({'kind': 'direct-http-import', 'native_loaded': False}))
        """)
        self.assertEqual(result, {"kind": "direct-http-import", "native_loaded": False})

    def test_runtime_import_has_preinit_policy_and_natural_exit(self):
        self._require("chromadb", "redis", "jsonschema", "prometheus_client")
        result = self._probe("""
            import os
            import echomind_core.runtime
            assert os.environ['ORT_DISABLE_TELEMETRY'] == '1'
            assert 'onnxruntime' in sys.modules
            print('LIFECYCLE_RESULT=' + json.dumps({'kind': 'runtime-import', 'native_loaded': True}))
        """)
        self.assertEqual(result, {"kind": "runtime-import", "native_loaded": True})

    def test_original_124_assertions_and_process_both_complete(self):
        self._require("pytest", "pytest_asyncio", "fakeredis", "mcp", "chromadb", "redis",
                      "jsonschema", "prometheus_client")
        result = self._probe("""
            import pytest
            class Results:
                warnings = 0
                def pytest_warning_recorded(self, warning_message, when, nodeid, location):
                    self.warnings += 1
                def pytest_sessionfinish(self, session, exitstatus):
                    self.collected = session.testscollected
                    self.failed = session.testsfailed
            results = Results()
            code = pytest.main(['echomind_core/upstream/tests', '-q', '-p', 'no:cacheprovider'], plugins=[results])
            assert code == 0 and results.collected == 124 and results.failed == 0
            print('LIFECYCLE_RESULT=' + json.dumps({'kind': 'unchanged-upstream', 'assertions': 124,
                                                  'warnings': results.warnings}))
        """, deadline=55)
        self.assertEqual({key: value for key, value in result.items() if key != "warnings"},
                         {"kind": "unchanged-upstream", "assertions": 124})
        self.assertIs(type(result.get("warnings")), int)
        self.assertTrue(0 <= result["warnings"] <= 1000)
        print(f"Lifecycle unchanged upstream: assertions=124; warnings={result['warnings']}; exit_code=0; natural_exit=true")

    def test_existing_cancel_drain_close_and_exception_cases_exit(self):
        self._require("chromadb", "redis", "jsonschema", "prometheus_client")
        if shutil.which("redis-server") is None:
            self.skipTest("requires an explicitly installed local redis-server for private Unix-socket fixtures")
        # Existing fixtures own a private Unix-socket Redis and independent
        # temporary counters only.  They never open the user's MySQL/SQLite.
        result = self._probe("""
            import shutil
            import unittest
            assert shutil.which('redis-server'), 'explicit_local_redis_dependency_required'
            names = [
                'tests.test_echomind_storage.StorageTests.test_real_collection_reuses_original_chunks_updates_and_owner_filter',
                'tests.test_echomind_storage.StorageTests.test_redis_failure_no_fallback_and_close_rejects',
                'tests.test_echomind_storage.StorageTests.test_cancel_worker_drains_before_release_and_repeat_cancel',
                'tests.test_echomind_storage.StorageTests.test_close_drains_active_collection_worker_before_releasing_lease',
                'tests.test_echomind_runtime.RuntimeTests.test_invalidating_direct_storage_embedding_cancels_inflight_request',
                'tests.test_echomind_execution.ExecutionTests.test_sync_handler_cancellation_drains_thread_before_scope_release',
            ]
            suite = unittest.defaultTestLoader.loadTestsFromNames(names)
            outcome = unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(suite)
            assert outcome.wasSuccessful() and outcome.testsRun == len(names) and not outcome.skipped
            print('LIFECYCLE_RESULT=' + json.dumps({'kind': 'controlled-cleanup', 'checks': len(names)}))
        """, deadline=55)
        self.assertEqual(result, {"kind": "controlled-cleanup", "checks": 6})

    def test_sigterm_becomes_cancellation_and_drains_before_natural_exit(self):
        self._require("chromadb", "redis")
        result = self._probe("""
            import asyncio
            import os
            import signal
            import threading
            from echomind_core.storage import _drain_thread

            async def main():
                entered, release, drained = threading.Event(), threading.Event(), threading.Event()
                def synchronous_work():
                    entered.set()
                    assert release.wait(2), 'controlled_worker_not_released'
                    drained.set()
                worker = asyncio.create_task(_drain_thread(synchronous_work))
                assert await asyncio.to_thread(entered.wait, 2), 'controlled_worker_not_started'
                signal_seen = asyncio.Event()
                def request_stop():
                    worker.cancel()
                    signal_seen.set()
                asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, request_stop)
                try:
                    os.kill(os.getpid(), signal.SIGTERM)
                    await asyncio.wait_for(signal_seen.wait(), 2)
                    await asyncio.sleep(.03)
                    assert not worker.done(), 'cancel_released_before_native_thread_drained'
                    worker.cancel()
                    release.set()
                    try:
                        await worker
                    except asyncio.CancelledError:
                        pass
                    else:
                        raise AssertionError('cancel_was_not_preserved')
                    assert drained.is_set()
                finally:
                    release.set()
                    asyncio.get_running_loop().remove_signal_handler(signal.SIGTERM)

            asyncio.run(main())
            print('LIFECYCLE_RESULT=' + json.dumps({'kind': 'cooperative-sigterm', 'worker_drained': True}))
        """)
        self.assertEqual(result, {"kind": "cooperative-sigterm", "worker_drained": True})


if __name__ == "__main__":
    unittest.main()
