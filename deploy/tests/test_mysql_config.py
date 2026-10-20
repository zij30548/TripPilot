"""Offline deployment selection tests; all URLs/passwords are synthetic."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

DEPLOY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("deploy_config_mysql_test", DEPLOY / "config.py")
assert spec and spec.loader
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)

URL = "mysql+pymysql://trippilot:synthetic_URLsafe_only_32_chars@127.0.0.1:3306/trippilot"
ANCHOR = "/var/lib/trippilot/usage.sqlite3.identity"


def values():
    return (config.parse((DEPLOY / "env/backend.env.example").read_text(), config.BACKEND),
            config.parse((DEPLOY / "env/frontend.env.example").read_text(), config.FRONTEND))


class MySQLDeploymentConfig(unittest.TestCase):
    def test_old_exact_required_keys_and_new_blank_template_both_still_work(self):
        back, front = values()
        config.validate(back, front)
        old = {key: value for key, value in back.items() if key in config.BACKEND}
        text = "\n".join(f"{key}='{value}'" for key, value in old.items())
        parsed = config.parse(text, config.BACKEND)
        self.assertEqual(set(parsed), config.BACKEND)
        config.validate(parsed, front)
        for removed in config.BACKEND:
            with self.assertRaises(config.ConfigError):
                config.parse("\n".join(line for line in text.splitlines() if not line.startswith(removed + "=")), config.BACKEND)

    def test_new_keys_are_backend_only_not_generic_optional_configuration(self):
        back, _ = values()
        self.assertEqual(set(back) - config.BACKEND, config.BACKEND_OPTIONAL)
        for field in config.BACKEND_OPTIONAL:
            with self.assertRaises(config.ConfigError): config.parse(f"{field}=", config.FRONTEND)
        template = (DEPLOY / "env/backend.env.example").read_text()
        for bad in ("TRIPPILOT_MYSQL_URL=\n", "BUSINESS_USAGE_IDENTITY_PATH=\n", "MYSQL_UNVALIDATED=\n"):
            with self.assertRaises(config.ConfigError): config.parse(template + "\n" + bad, config.BACKEND)

    def test_fixed_loopback_database_and_existing_anchor_are_required(self):
        back, front = values()
        back.update(TRIPPILOT_MYSQL_URL=URL, BUSINESS_USAGE_IDENTITY_PATH=ANCHOR)
        config.validate(back, front)
        back["TRIPPILOT_MYSQL_URL"] = URL + "?charset=utf8mb4"
        config.validate(back, front)
        back["BUSINESS_USAGE_IDENTITY_PATH"] = ""
        with self.assertRaisesRegex(config.ConfigError, "production_usage_anchor_required"): config.validate(back, front)

    def test_unsafe_urls_paths_and_expansions_are_rejected_without_echo(self):
        invalid = (
            URL.replace("mysql+pymysql", "mysql"), URL.replace("127.0.0.1", "mysql.example.invalid"),
            URL.replace("127.0.0.1", "localhost"), URL.replace(":3306", ":3307"),
            URL.replace("/trippilot", "/other_database"), URL + "/", URL + "?ssl_disabled=true",
            URL + "#private", URL.replace("synthetic_URLsafe_only_32_chars", "short"),
            URL.replace("synthetic_URLsafe_only_32_chars", "REPLACE_URL_SAFE_PASSWORD"),
            URL.replace("synthetic_URLsafe_only_32_chars", "private%40encoded"),
            URL.replace("trippilot:", "root-user:"), URL.replace("trippilot:", "root:"),
            URL + "?charset=utf8mb4&host=other",
        )
        for raw in invalid:
            with self.subTest(case=invalid.index(raw)):
                back, front = values()
                back.update(TRIPPILOT_MYSQL_URL=raw, BUSINESS_USAGE_IDENTITY_PATH=ANCHOR)
                with self.assertRaisesRegex(config.ConfigError, "production_loopback_mysql_required") as error:
                    config.validate(back, front)
                self.assertNotIn("synthetic_URLsafe", str(error.exception))
                self.assertNotIn(raw, str(error.exception))
        back, front = values()
        back["BUSINESS_USAGE_IDENTITY_PATH"] = "/tmp/new-allowance.identity"
        with self.assertRaises(config.ConfigError): config.validate(back, front)

    def test_mysql_preflight_remains_stdlib_only_without_credentials_in_output(self):
        code = """
import importlib.util, pathlib, sys
directory = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('offline_config', directory / 'config.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
back = module.parse((directory / 'env/backend.env.example').read_text(), module.BACKEND)
front = module.parse((directory / 'env/frontend.env.example').read_text(), module.FRONTEND)
back['TRIPPILOT_MYSQL_URL'] = 'mysql+pymysql://trippilot:synthetic_URLsafe_only_32_chars@127.0.0.1:3306/trippilot'
back['BUSINESS_USAGE_IDENTITY_PATH'] = '/var/lib/trippilot/usage.sqlite3.identity'
module.validate(back, front)
assert not {'app','pymysql','sqlalchemy','pydantic'}.intersection(sys.modules)
print('stdlib MySQL deployment configuration OK')
"""
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(DEPLOY)], capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(result.stdout.strip(), "stdlib MySQL deployment configuration OK")
        self.assertNotIn("synthetic_URLsafe", result.stdout + result.stderr)


if __name__ == "__main__": unittest.main()
