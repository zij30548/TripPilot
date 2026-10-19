"""Offline deployment/backend URL parity; all URLs and keys are fictitious."""
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

DEPLOY = Path(__file__).resolve().parents[1]
ROOT = DEPLOY.parent
spec = importlib.util.spec_from_file_location("deploy_config_qwen_test", DEPLOY / "config.py")
assert spec and spec.loader
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)

VALID_URLS = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1/",
    "https://llm-offline.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "https://llm-offline.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/",
    "https://a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "https://0.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "https://" + "a" * 63 + ".cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "HTTPS://LLM-OFFLINE.CN-BEIJING.MAAS.ALIYUNCS.COM/compatible-mode/v1",
)
INVALID_URLS = (
    "http://dashscope.aliyuncs.com/compatible-mode/v1",
    "//dashscope.aliyuncs.com/compatible-mode/v1",
    "https://example.invalid/compatible-mode/v1",
    "https://dashscope.aliyuncs.com.example.invalid/compatible-mode/v1",
    "https://llm-offline.cn-beijing.maas.aliyuncs.com.example.invalid/compatible-mode/v1",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "https://llm-offline.cn-shanghai.maas.aliyuncs.com/compatible-mode/v1",
    "https://llm-offline.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    "https://cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "https://one.two.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "https://-offline.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "https://offline-.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "https://off_line.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "https://" + "a" * 64 + ".cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "https://dashscope.aliyuncs.com:443/compatible-mode/v1",
    "https://llm-offline.cn-beijing.maas.aliyuncs.com:8443/compatible-mode/v1",
    "https://dashscope.aliyuncs.com:bad/compatible-mode/v1",
    "https://dashscope.aliyuncs.com:65536/compatible-mode/v1",
    "https://user@dashscope.aliyuncs.com/compatible-mode/v1",
    "https://user:password@dashscope.aliyuncs.com/compatible-mode/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1?key=offline",
    "https://dashscope.aliyuncs.com/compatible-mode/v1#offline",
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "https://dashscope.aliyuncs.com/compatible-mode/v2",
    "https://dashscope.aliyuncs.com/compatible-mode/v1//",
    "https://dashscope.aliyuncs.com/compatible-mode%2Fv1",
    "https://dashscope.aliyuncs.com/Compatible-mode/v1",
    " https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1 ",
    "https://dashscope.aliyuncs.com\\/compatible-mode/v1",
    "https://[broken/compatible-mode/v1",
)
# Preserve the deployment check's strict URL syntax even when urlsplit would
# discard an empty separator or a control character. Do not broaden the backend.
STRICT_DEPLOY_REJECTIONS = (
    "https://dashscope.aliyuncs.com:/compatible-mode/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1?",
    "https://dashscope.aliyuncs.com/compatible-mode/v1#",
    "https://dashscope.aliyuncs.com/compatible-mode/\tv1",
    "https://dashscope.aliyuncs.com/compatible-mode/\nv1",
)


def deployment_values(url: str) -> tuple[dict[str, str], dict[str, str]]:
    back = config.parse((DEPLOY / "env/backend.env.example").read_text(), config.BACKEND)
    front = config.parse((DEPLOY / "env/frontend.env.example").read_text(), config.FRONTEND)
    back["QWEN_BASE_URL"] = url
    return back, front


class QwenDeploymentConfig(unittest.TestCase):
    def test_public_and_workspace_url_matrix(self) -> None:
        for url in VALID_URLS:
            with self.subTest(url=url):
                back, front = deployment_values(url)
                config.validate(back, front)
        for url in (*INVALID_URLS, *STRICT_DEPLOY_REJECTIONS):
            with self.subTest(url=url):
                back, front = deployment_values(url)
                with self.assertRaisesRegex(config.ConfigError, "^beijing_model_origin_required$"):
                    config.validate(back, front)

    def test_nonempty_host_path_matrix_matches_actual_backend_boundary(self) -> None:
        # Import the real backend only in this test, never from deploy/config.py.
        with patch.object(sys, "path", [str(ROOT / "backend"), *sys.path]):
            from app.integrations.qwen import QwenError, beijing_endpoint
        from pydantic import SecretStr
        for accepted, urls in ((True, VALID_URLS), (False, INVALID_URLS)):
            for url in urls:
                with self.subTest(url=url):
                    settings = SimpleNamespace(qwen_base_url=url, qwen_api_key=SecretStr("offline-not-a-real-key"))
                    back, front = deployment_values(url)
                    if accepted:
                        config.validate(back, front)
                        endpoint = beijing_endpoint(settings)
                        self.assertTrue(endpoint.endswith("/compatible-mode/v1/chat/completions"))
                    else:
                        with self.assertRaises(config.ConfigError): config.validate(back, front)
                        with self.assertRaises(QwenError) as error: beijing_endpoint(settings)
                        self.assertEqual(error.exception.code, "configuration")

    def test_blank_is_still_optional_deployment_configuration(self) -> None:
        back, front = deployment_values("")
        config.validate(back, front)
        self.assertEqual(back["QWEN_API_KEY"], "")
        self.assertEqual(back["QWEN_MODEL"], "qwen3.7-flash-2026-07-15")
        self.assertEqual(back["QWEN_VISION_MODEL"], "qwen3-vl-plus")

    def test_error_never_includes_submitted_url_or_credentials(self) -> None:
        back, front = deployment_values("https://offline-private:never-log@invalid.example/?key=never-log")
        with self.assertRaises(config.ConfigError) as error: config.validate(back, front)
        self.assertEqual(str(error.exception), "beijing_model_origin_required")
        self.assertIsNone(error.exception.__cause__)

    def test_initial_deployment_check_requires_only_standard_library(self) -> None:
        code = """
import importlib.util, pathlib, sys
directory = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('offline_deploy_config', directory / 'config.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
back = module.parse((directory / 'env/backend.env.example').read_text(), module.BACKEND)
front = module.parse((directory / 'env/frontend.env.example').read_text(), module.FRONTEND)
back['QWEN_BASE_URL'] = 'https://llm-offline.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/'
module.validate(back, front)
assert not {'app', 'httpx', 'pydantic'}.intersection(sys.modules)
print('stdlib deployment config OK')
"""
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(DEPLOY)],
                                capture_output=True, text=True, check=True, timeout=10)
        self.assertEqual(result.stdout.strip(), "stdlib deployment config OK")


if __name__ == "__main__": unittest.main()
