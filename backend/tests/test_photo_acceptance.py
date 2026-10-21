import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tooling.providers.qwen import QwenError
from tools.photo_acceptance import PhotoLedger, IDS, digest, canonical, MODEL


class AccountingTests(unittest.TestCase):
    def ledger(self, directory):
        (directory/"protocol.json").write_text(json.dumps({"limit":12,"model":MODEL,"cases":[{"id":i,"payload_hash":digest(canonical({"test":i}))} for i in IDS]}))
        return PhotoLedger(directory)

    def test_twelve_shared_attempts_persist_no_repeat_and_no_thirteenth(self):
        with tempfile.TemporaryDirectory() as d, patch.object(PhotoLedger,"verify"):
            path=Path(d);ledger=self.ledger(path)
            for index,operation in enumerate(IDS):
                row={"event":"attempt","operation":operation,"payload_hash":digest(canonical({"test":operation}))}
                ledger.append(row,True)
                # Uncertain attempt prevents next operation (including after restart).
                if index+1<len(IDS):
                    with self.assertRaises(QwenError): PhotoLedger(path).append({"event":"attempt","operation":IDS[index+1],"payload_hash":digest(canonical({"test":IDS[index+1]}))},True)
                ledger.append({"event":"completed","operation":operation,"pause":False})
                with self.assertRaises(QwenError): PhotoLedger(path).append(row,True)
            rows=[json.loads(s) for s in (path/"calls.jsonl").read_text().splitlines()]
            self.assertEqual(len([r for r in rows if r["event"]=="attempt"]),12)
            with self.assertRaises(QwenError): ledger.append({"event":"attempt","operation":"extra","payload_hash":"x"},True)

    def test_execution_pause_and_version_changes_block(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=self.ledger(Path(d));row={"event":"attempt","operation":IDS[0],"payload_hash":digest(canonical({"test":IDS[0]}))}
            with patch.object(PhotoLedger,"verify"):
                ledger.append(row,True);ledger.append({"event":"completed","operation":IDS[0],"pause":True,"error":"timeout"})
                with self.assertRaises(QwenError): ledger.append({"event":"attempt","operation":IDS[1],"payload_hash":digest(canonical({"test":IDS[1]}))},True)
            (Path(d)/"protocol.json").write_text("{}")
            with self.assertRaises(QwenError): ledger.verify()
