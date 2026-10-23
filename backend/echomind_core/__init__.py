"""Preserved EchoMind core; active only through explicit product admission.

The upstream algorithms live in ``upstream`` with a verifiable provenance map.
This stdlib-only preflight disables native telemetry before optional imports.
Imports here never start Redis, Chroma, MCP, a model call or a background task,
and do not themselves import ONNX or any optional dependency.
Only the explicit Qwen adapter may send model requests, under the existing
durable usage context. Default legacy app imports remain independent of this
package; opt-in admission treats unavailable/late policy as a dependency error.
"""

from echomind_core.native_runtime import configure_native_runtime as _configure_native_runtime

_configure_native_runtime()

VERSION = "echomind-full-core-stage1"
PRODUCT_ENABLED = False
