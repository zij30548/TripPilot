"""Offline local-file/Mock-inner checks; no model, AMap or database calls."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest

import httpx

from core.config import Settings
from tools.chat_opening_repair import OpeningRepair, RepairStopped, MODEL


class OpeningRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.folder.chmod(0o700)
        self.source = self.folder / "runtime.py"
        self.source.write_bytes(b"# diagnostic version\n")
        self.settings = Settings(_env_file=None, qwen_api_key="controlled-not-real",
            qwen_base_url="https://workspace-7.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
        self.options = {"sources": {"runtime": self.source}, "prompt_sha256": "b" * 64,
                        "schema_sha256": "c" * 64}
        self.bindings = {"grant_sha256": "a" * 64, "prior_ledger_hashes": {"old_paused": "d" * 64}}
        self.trial = OpeningRepair.create(self.folder / "sending", self.settings, **self.bindings)
        self.endpoint = "https://workspace-7.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
        self.sent = []

    async def handle(self, request):
        self.sent.append(request)
        return httpx.Response(200, json={"choices": [], "private_output": "NOT_SAVED"})

    async def send(self, operation="diag01", *, inner=None, endpoint=None, options=None):
        transport = self.trial.transport(operation, inner=inner or httpx.MockTransport(self.handle),
                                         **(options or self.options))
        async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False) as client:
            return await client.post(endpoint or self.endpoint, headers={"Authorization": "Bearer DO_NOT_SAVE"}, json={
                "model": MODEL, "stream": False, "enable_thinking": False,
                "messages": [{"role": "user", "content": "PRIVATE_USER_INPUT_NOT_SAVED"}]})

    def freeze(self, phase="diagnostic", options=None):
        return self.trial.freeze_phase(phase, **(options or self.options))

    def test_batch_is_new_private_and_binding_open_is_read_only(self):
        original = (self.trial.folder / "binding.json").read_bytes()
        reopened = OpeningRepair.open(self.trial.folder, self.settings, **self.bindings)
        reopened.verify()
        self.assertEqual((self.trial.folder / "binding.json").read_bytes(), original)
        self.assertEqual(self.trial.folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.trial.folder / "binding.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(RepairStopped, "must_be_new"):
            OpeningRepair.create(self.trial.folder, self.settings, **self.bindings)
        with self.assertRaises(RepairStopped):
            OpeningRepair.open(self.trial.folder, self.settings, **(self.bindings | {"grant_sha256": "e" * 64}))

    async def test_one_claim_received_and_no_secret_or_raw_body_recorded(self):
        self.freeze()
        response = await self.send()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("PRIVATE_USER_INPUT", self.sent[0].content.decode())
        combined = b"".join(path.read_bytes() for path in self.trial.folder.iterdir())
        for forbidden in (b"PRIVATE_USER_INPUT", b"NOT_SAVED", b"Bearer", b"Authorization"):
            self.assertNotIn(forbidden, combined)
        self.assertEqual(json.loads((self.trial.folder / "diag01-result.json").read_bytes())["state"], "received")
        with self.assertRaises(RepairStopped):
            await self.send()
        self.trial = OpeningRepair.open(self.trial.folder, self.settings, **self.bindings)
        with self.assertRaises(RepairStopped):
            await self.send()
        self.assertEqual(len(self.sent), 1)

    async def test_concurrent_duplicate_only_one_inner_send(self):
        self.freeze()
        results = await asyncio.gather(self.send(), self.send(), return_exceptions=True)
        self.assertEqual(sum(isinstance(x, httpx.Response) for x in results), 1)
        self.assertEqual(sum(isinstance(x, RepairStopped) for x in results), 1)
        self.assertEqual(len(self.sent), 1)

    async def test_changed_phase_prompt_schema_source_rejected_before_claim(self):
        self.freeze()
        for key in ("prompt_sha256", "schema_sha256"):
            with self.subTest(key=key), self.assertRaises(RepairStopped):
                await self.send(options=self.options | {key: "e" * 64})
        self.source.write_bytes(b"# changed\n")
        with self.assertRaises(RepairStopped):
            await self.send()
        self.assertFalse((self.trial.folder / "diag01-claim.json").exists())
        self.assertEqual(self.sent, [])

    async def test_diagnostic_and_final_phases_preserved_and_four_operations_only(self):
        self.freeze()
        diagnostic = (self.trial.folder / "diagnostic-phase.json").read_bytes()
        await self.send()
        self.source.write_bytes(b"# fixed final version\n")
        options = self.options | {"prompt_sha256": "f" * 64}
        self.freeze("final", options)
        for operation in ("verify01", "verify02", "chrome01"):
            await self.send(operation, options=options)
        self.assertEqual(len(self.sent), 4)
        self.assertEqual((self.trial.folder / "diagnostic-phase.json").read_bytes(), diagnostic)
        with self.assertRaises(RepairStopped):
            self.freeze("final", options)
        with self.assertRaises(RepairStopped):
            await self.send("extra01", options=options)
        with self.assertRaises(RepairStopped):
            await self.send("verify01", options=options)
        self.assertEqual(len(self.sent), 4)

    async def test_wrong_endpoints_and_model_configuration_never_send(self):
        self.freeze()
        for endpoint in ("https://evil.test/x", self.endpoint + "?x=1", self.endpoint.replace("https:", "http:"),
                         self.endpoint.replace(".com/", ".com:8443/"), self.endpoint + "#private"):
            with self.subTest(endpoint=endpoint), self.assertRaises(RepairStopped):
                await self.send(endpoint=endpoint)
        self.assertEqual(self.sent, [])
        self.assertFalse((self.trial.folder / "diag01-claim.json").exists())
        for update in ({"qwen_base_url": "https://evil.test/compatible-mode/v1"},
                       {"qwen_base_url": "https://dashscope.aliyuncs.com:443/compatible-mode/v1"},
                       {"qwen_model": "qwen3.7-plus-2026-05-26"}):
            with self.assertRaises(RepairStopped):
                OpeningRepair.open(self.trial.folder, self.settings.model_copy(update=update), **self.bindings)

    async def test_unknown_attempt_and_network_failure_do_not_recover_credit(self):
        self.freeze()
        (self.trial.folder / "diag01-claim.json").write_bytes(b'{"state":"claimed_before_send"}')
        with self.assertRaisesRegex(RepairStopped, "uncertain_attempt"):
            OpeningRepair.open(self.trial.folder, self.settings, **self.bindings)
        self.assertEqual(self.sent, [])

    async def test_network_error_and_sent_cancellation_block_new_sends(self):
        self.freeze()
        entered = asyncio.Event()
        async def hold(request):
            self.sent.append(request)
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(self.send(inner=httpx.MockTransport(hold)))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        record = json.loads((self.trial.folder / "diag01-result.json").read_bytes())
        self.assertEqual(record, {"state": "cancelled", "sent": True})
        with self.assertRaisesRegex(RepairStopped, "paused"):
            await self.send()
        self.assertEqual(len(self.sent), 1)

    async def test_transport_exception_is_retained_and_not_automatically_retried(self):
        self.freeze()
        async def fail(request):
            self.sent.append(request)
            raise httpx.ConnectError("private upstream error must not be logged", request=request)
        with self.assertRaises(httpx.ConnectError):
            await self.send(inner=httpx.MockTransport(fail))
        with self.assertRaises(RepairStopped):
            await self.send()
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn(b"private upstream", (self.trial.folder / "diag01-result.json").read_bytes())

    async def test_partial_or_uncertain_receipt_never_unlocks_a_new_operation(self):
        self.freeze()
        await self.send()
        result = self.trial.folder / "diag01-result.json"
        for invalid in (b'{"state":', b'{"state":"received"}', b'{"state":"unknown","sent":true}'):
            result.write_bytes(invalid)
            with self.subTest(invalid=invalid), self.assertRaises(RepairStopped):
                OpeningRepair.open(self.trial.folder, self.settings, **self.bindings)
        self.assertEqual(len(self.sent), 1)

    def test_source_symlink_hardlink_and_batch_permissions_fail_closed(self):
        linked = self.folder / "linked.py"
        linked.symlink_to(self.source)
        with self.assertRaises(RepairStopped):
            self.freeze(options=self.options | {"sources": {"linked": linked}})
        hardlink = self.folder / "hard.py"
        os.link(self.source, hardlink)
        with self.assertRaises(RepairStopped):
            self.freeze()
        hardlink.unlink()
        self.trial.folder.chmod(0o755)
        with self.assertRaises(RepairStopped):
            self.trial.verify()


if __name__ == "__main__":
    unittest.main()
