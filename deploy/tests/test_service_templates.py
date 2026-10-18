"""Offline assertions only; not a substitute for systemd-analyze/nginx -t on Ubuntu."""

import ast
import configparser
from pathlib import Path
import re
import shlex
import unittest


DEPLOY = Path(__file__).resolve().parents[1]


def unit(name: str) -> configparser.ConfigParser:
    result = configparser.ConfigParser(interpolation=None, strict=False)
    result.read(DEPLOY / "systemd" / name, encoding="utf-8")
    return result


class ServiceTemplateTests(unittest.TestCase):
    def test_video_default_hidden_until_explicit_compatibility_dropin(self) -> None:
        api = unit("trippilot-api.service")["Service"]
        expected = {
            "-" + folder + "/" + name
            for folder in ("/usr/bin", "/usr/local/bin", "/opt/homebrew/bin")
            for name in ("ffmpeg", "ffprobe")
        }
        self.assertEqual(set(api["InaccessiblePaths"].split()), {"/var/backups/trippilot", *expected})
        # A future production trusted-directory expansion must update the deployment
        # denylist too; do not silently leave an unchecked decoder reachable.
        source = DEPLOY.parent / "backend/app/services/video.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "executables")
        loop = next(node for node in function.body if isinstance(node, ast.For))
        self.assertEqual(set(ast.literal_eval(loop.iter)), {"/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"})
        dropin = (DEPLOY / "systemd/video-enabled.conf.example").read_text(encoding="utf-8")
        directives = [line.strip() for line in dropin.splitlines() if line.startswith("InaccessiblePaths=")]
        self.assertEqual(directives, ["InaccessiblePaths=", "InaccessiblePaths=/var/backups/trippilot"])

    def test_exact_loopback_commands_and_one_api_worker(self) -> None:
        api = unit("trippilot-api.service")["Service"]
        web = unit("trippilot-web.service")["Service"]
        api_command = shlex.split(api["ExecStart"])
        self.assertEqual(api_command[:4], [
            "/opt/trippilot/current/backend/.venv/bin/python", "-B", "-m", "uvicorn",
        ])
        self.assertEqual(api_command[api_command.index("--host") + 1], "127.0.0.1")
        self.assertEqual(api_command[api_command.index("--port") + 1], "8000")
        self.assertEqual(api_command[api_command.index("--workers") + 1], "1")
        self.assertIn("--no-access-log", api_command)
        self.assertIn("--no-proxy-headers", api_command)
        web_command = shlex.split(web["ExecStart"])
        self.assertEqual(web_command[:3], [
            "/usr/bin/node", "/opt/trippilot/current/frontend/node_modules/next/dist/bin/next", "start",
        ])
        self.assertEqual(web_command[web_command.index("--hostname") + 1], "127.0.0.1")
        self.assertEqual(web_command[web_command.index("--port") + 1], "3000")

    def test_identity_secret_and_persistent_data_separation(self) -> None:
        api = unit("trippilot-api.service")["Service"]
        web = unit("trippilot-web.service")["Service"]
        self.assertEqual(api["User"], "trippilot-api")
        self.assertEqual(web["User"], "trippilot-web")
        self.assertEqual(api["EnvironmentFile"], "/etc/trippilot/backend.env")
        self.assertEqual(web["EnvironmentFile"], "/etc/trippilot/frontend.env")
        self.assertEqual(api["ReadWritePaths"], "/var/lib/trippilot")
        self.assertEqual(web["ReadWritePaths"], "/opt/trippilot/current/frontend/.next/cache")
        inaccessible = set(web["InaccessiblePaths"].split())
        self.assertTrue({"/etc/trippilot/backend.env", "/var/lib/trippilot", "/var/backups/trippilot"} <= inaccessible)
        for service in (api, web):
            self.assertEqual(service["ProtectHome"], "true")
            self.assertEqual(service["ProtectSystem"], "strict")
            self.assertEqual(service["NoNewPrivileges"], "true")
            self.assertEqual(service["UMask"], "0077")
        self.assertEqual(shlex.split(api["ExecStartPre"]), [
            "/opt/trippilot/current/backend/.venv/bin/python", "-B",
            "/opt/trippilot/current/deploy/data_admin.py", "verify",
        ])
        self.assertNotIn("ExecStartPre", web)

    def test_bounded_restart_and_child_cleanup(self) -> None:
        for name in ("trippilot-api.service", "trippilot-web.service"):
            parsed = unit(name)
            self.assertEqual(parsed["Unit"]["StartLimitBurst"], "3")
            service = parsed["Service"]
            self.assertEqual(service["Restart"], "on-failure")
            self.assertEqual(service["KillMode"], "control-group")
            self.assertEqual(service["SendSIGKILL"], "yes")
            self.assertEqual(service["TasksMax"], "128")
            self.assertEqual(service["StandardOutput"], "journal")
            self.assertNotIn("MemoryDenyWriteExecute", service)


class NginxTemplateTests(unittest.TestCase):
    def text(self, name: str) -> str:
        text = (DEPLOY / "nginx" / name).read_text(encoding="utf-8")
        return "\n".join(line.split("#", 1)[0] for line in text.splitlines())

    def test_bootstrap_cannot_expose_http_application_or_missing_tls(self) -> None:
        text = self.text("trippilot-http.conf")
        self.assertNotIn("ssl_certificate", text)
        self.assertNotIn("proxy_pass", text)
        self.assertIn("return 503", text)
        self.assertIn("location ^~ /.well-known/acme-challenge/", text)
        self.assertIn("root /var/www/trippilot-acme;", text)

    def test_tls_only_fixed_upstream_no_retry_or_buffered_upload(self) -> None:
        text = self.text("trippilot-https.conf")
        self.assertEqual(re.findall(r"proxy_pass\s+([^;]+);", text), ["http://127.0.0.1:3000"])
        self.assertIn("server_name trip.zijinglab.com;", text)
        self.assertIn("return 308 https://trip.zijinglab.com$request_uri;", text)
        self.assertIn("ssl_certificate /etc/letsencrypt/live/trip.zijinglab.com/fullchain.pem;", text)
        for directive in (
            "proxy_http_version 1.1", "proxy_request_buffering off", "proxy_buffering off",
            "proxy_ignore_client_abort off", "proxy_next_upstream off", "proxy_intercept_errors off",
            "proxy_send_timeout 90s", "proxy_read_timeout 90s", "proxy_set_header X-Forwarded-Proto https",
        ):
            self.assertIn(directive + ";", text)

    def test_upload_boundary_and_no_sensitive_per_request_logs(self) -> None:
        for name in ("trippilot-http.conf", "trippilot-https.conf"):
            text = self.text(name)
            self.assertIn("client_max_body_size 21m;", text)
            self.assertIn("access_log off;", text)
            self.assertIn("error_log /dev/null crit;", text)
            self.assertNotRegex(text, r"(?:access|error)_log\s+(?!off|/dev/null)")
            self.assertNotIn("$http_authorization", text)
            self.assertNotIn("$http_cookie", text)
            self.assertNotIn("log_format", text)


if __name__ == "__main__":
    unittest.main()
