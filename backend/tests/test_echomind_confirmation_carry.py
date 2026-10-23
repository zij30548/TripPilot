"""Confirmation carry is one trusted running operation, not value-based ABA."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import unittest

from memory.chat_storage import ChatError, ConfirmationCarry
from tests import test_chat_storage as fixtures
from tests.archive_owner import OWNER


class ConfirmationCarryTests(unittest.TestCase):
    setUp = fixtures.ChatStorage.setUp
    tearDown = fixtures.ChatStorage.tearDown
    create = fixtures.ChatStorage.create
    complete = fixtures.ChatStorage.complete

    def confirmed(self):
        view = self.complete(self.create())
        return self.store.confirm(OWNER, view["id"], view["revision"], fixtures.operation())

    def begin(self, view, *, capture=True):
        op = fixtures.operation()
        return op, self.store.begin_turn(OWNER, view["id"], op, view["revision"], "查看输入",
                                         capture_confirmation=capture)

    def finish(self, view, op, begun, *, requirements=None, sources=None, issues=None, carry=True):
        return self.store.finish_turn(OWNER, view["id"], op, begun["revision"],
            requirements or view["requirements"], "程序核对结果", view["sources"] if sources is None else sources,
            view["issues"] if issues is None else issues,
            **({"confirmation_carry": begun["confirmation_carry"]} if carry else {}))

    def test_same_inputs_carry_only_the_transaction_captured_confirmation(self):
        view = self.confirmed()
        op, begun = self.begin(view)
        self.assertIsNone(begun["conversation"]["confirmed_revision"])
        proof = begun["confirmation_carry"]
        self.assertIs(type(proof), ConfirmationCarry)
        with self.assertRaises(FrozenInstanceError):
            proof.frozen_revision = 0
        result = self.finish(view, op, begun)
        self.assertEqual(result["confirmed_revision"], result["revision"])
        self.assertEqual(result["requirements"], view["requirements"])
        self.assertEqual(result["issues"], view["issues"])

    def test_legacy_default_remains_clear_and_draft_cannot_gain_confirmation(self):
        view = self.confirmed()
        op = fixtures.operation()
        begun = self.store.begin_turn(OWNER, view["id"], op, view["revision"], "旧调用")
        self.assertNotIn("confirmation_carry", begun)
        result = self.finish(view, op, begun, carry=False)
        self.assertIsNone(result["confirmed_revision"])
        op, next_begun = self.begin(result)
        self.assertIsNone(next_begun["confirmation_carry"])
        self.assertIsNone(self.finish(result, op, next_begun)["confirmed_revision"])

    def test_changed_requirements_issues_or_sources_do_not_inherit(self):
        for change in ({"requirements": {**fixtures.intent(), "travelers": 3}},
                       {"issues": ["unimplemented_preference"]},
                       {"sources": [{"field": "destination_id", "quote": "杭州"}]}):
            with self.subTest(change=change):
                view = self.confirmed()
                op, begun = self.begin(view)
                result = self.finish(view, op, begun, **change)
                self.assertIsNone(result["confirmed_revision"])

    def test_client_dict_wrong_owner_operation_and_revision_cannot_supply_authority(self):
        view = self.confirmed()
        op, begun = self.begin(view)
        proof = begun["confirmation_carry"]
        for invalid in ({"confirmed": True}, replace(proof, owner_id="b" * 32),
                        replace(proof, operation_id=fixtures.operation()), replace(proof, frozen_revision=proof.frozen_revision + 1)):
            with self.subTest(invalid=type(invalid).__name__):
                with self.assertRaisesRegex(ChatError, "chat_confirmation_invalid"):
                    self.store.finish_turn(OWNER, view["id"], op, begun["revision"], view["requirements"], "结果", view["sources"],
                                           confirmation_carry=invalid)
        with self.assertRaisesRegex(ChatError, "chat_confirmation_invalid"):
            ConfirmationCarry(OWNER, view["id"], op, begun["revision"], fixtures.operation(), "fake", object())

    def test_cancel_failure_and_aba_cannot_restore_old_confirmation_or_complete_twice(self):
        view = self.confirmed()
        op, begun = self.begin(view)
        cancelled = self.store.cancel_turn(OWNER, view["id"], op)
        self.assertIsNone(cancelled["confirmed_revision"])
        newer_op, newer = self.begin(cancelled)
        with self.assertRaisesRegex(ChatError, "chat_turn_obsolete"):
            self.finish(view, op, begun)
        completed = self.finish(cancelled, newer_op, newer)
        self.assertIsNone(completed["confirmed_revision"])
        with self.assertRaises(ChatError):
            self.finish(cancelled, newer_op, newer)

    def test_concurrent_finish_has_one_cas_winner_and_does_not_create_a_second_confirmation(self):
        view = self.confirmed()
        op, begun = self.begin(view)
        def commit(_):
            try:
                return self.finish(view, op, begun)
            except ChatError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(commit, (1, 2)))
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertIn("chat_turn_obsolete", results)
        latest = self.store.get(OWNER, view["id"])
        self.assertEqual(latest["confirmed_revision"], latest["revision"])


if __name__ == "__main__":
    unittest.main()
