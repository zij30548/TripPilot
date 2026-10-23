"""Pre-initialization policy for the candidate's optional native dependency.

The installed ONNX Runtime 1.31 Privacy.md documents
``ORT_DISABLE_TELEMETRY=1`` as the process-lifetime 1DS opt-out.  The public
``disable_telemetry_events`` API is not an equivalent pre-initialization guard:
an initialization event may already exist before that API can be called.

This module deliberately uses only the standard library.  It does not import
ONNX, Chroma, a provider, or an inference session, and cannot retroactively make
an already imported native runtime safe.  Configure before optional imports;
an explicit conflicting operator value is an error, not silently overwritten.
"""
from __future__ import annotations

import os
import sys
import threading

VERSION = "echomind-native-preinit-v1"
TELEMETRY_ENV = "ORT_DISABLE_TELEMETRY"
_LOCK = threading.Lock()
_configured = False


class NativeRuntimeError(ImportError):
    """A fixed, bounded policy error, without environment or exception details."""

    def __init__(self, code: str) -> None:
        if code not in {
            "native_telemetry_policy_conflict",
            "native_runtime_already_initialized",
            "native_telemetry_policy_changed",
            "native_telemetry_policy_unavailable",
        }:
            code = "native_telemetry_policy_unavailable"
        self.code = code
        super().__init__(code)


def configure_native_runtime() -> None:
    """Install the official ONNX opt-out before its first Python import.

    Idempotence is process-local and serialized, not a way to ignore changes:
    later calls require the protected value to remain exactly ``"1"``.  A
    caller must stop optional startup if this raises.  No shutdown hooks or
    forced-exit behavior are installed, and dependency absence is harmless.
    """
    global _configured
    with _LOCK:
        value = os.environ.get(TELEMETRY_ENV)
        if _configured:
            if value != "1":
                raise NativeRuntimeError("native_telemetry_policy_changed")
            return
        if value not in (None, "1"):
            raise NativeRuntimeError("native_telemetry_policy_conflict")
        if any(name == "onnxruntime" or name.startswith("onnxruntime.")
               for name in tuple(sys.modules)):
            raise NativeRuntimeError("native_runtime_already_initialized")
        try:
            os.environ[TELEMETRY_ENV] = "1"
        except (OSError, ValueError):
            raise NativeRuntimeError("native_telemetry_policy_unavailable") from None
        if os.environ.get(TELEMETRY_ENV) != "1":
            raise NativeRuntimeError("native_telemetry_policy_unavailable")
        _configured = True
