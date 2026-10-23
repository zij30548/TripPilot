"""Full EchoMind core migration candidate, NOT the active product runtime.

The upstream algorithms live in ``upstream`` with a verifiable provenance map.
Imports here never start Redis, Chroma, MCP, a model call or a background task.
Only the explicit Qwen adapter may send model requests, under the existing
durable usage context. No production route imports this candidate in stage 1.
"""

VERSION = "echomind-full-core-stage1"
PRODUCT_ENABLED = False
