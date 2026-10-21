"""Offline provider-schema compatibility checks, not real-model quality scores.

The historical failure remains a semantic failure. These tests reproduce a
search/full-match ambiguity and protect the narrow outbound-schema correction;
they neither call a supplier nor infer how its constrained decoder is built.
"""
import copy
from datetime import datetime, timezone
from hashlib import sha256
import itertools
import json
import re
from typing import Annotated
import unittest
from unittest.mock import patch

from pydantic import StringConstraints, TypeAdapter, ValidationError

from app.schemas.chat import Intent, Selection, ShortText
from app.services.chat import (
    PROMPT_VERSION, PROVIDER_SCHEMA_VERSION, apply_selection, model_payload,
    provider_schema,
)


PATTERN_PATHS = (
    ("$defs", "Intent", "properties", "accommodation_hint", "anyOf", 0, "pattern"),
    ("$defs", "Intent", "properties", "required_place_hints", "items", "pattern"),
    ("$defs", "Intent", "properties", "preferences", "items", "pattern"),
)
# Captured from the pre-fix builder with the controlled input in test_payload.
# No private transcript, expectation, credential or acceptance payload is read.
PRE_FIX_PAYLOAD_WITHOUT_SCHEMA_SHA256 = "c2b64a432357e4486fdf206237e039420ef2a5c6bb47416f8c23197581ea00e3"


def legacy_provider_schema() -> dict:
    schema = Selection.model_json_schema()
    for definition in schema.get("$defs", {}).values():
        if definition.get("type") == "object":
            definition["required"] = list(definition.get("properties", {}))
    return schema


def at_path(value: dict, path: tuple) -> object:
    for key in path:
        value = value[key]
    return value


def different_paths(before: object, after: object, path: tuple = ()) -> set[tuple]:
    if type(before) is not type(after):
        return {path}
    if isinstance(before, dict):
        if before.keys() != after.keys():
            return {path}
        return set().union(*(different_paths(before[key], after[key], path + (key,)) for key in before))
    if isinstance(before, list):
        if len(before) != len(after):
            return {path}
        return set().union(*(different_paths(left, right, path + (index,))
                             for index, (left, right) in enumerate(zip(before, after))))
    return set() if before == after else {path}


class ChatProviderSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        # Tests must fail before any accidental external send, including a
        # supplier call hidden behind a future helper refactor.
        self.send_guard = patch("httpx.AsyncClient.send", side_effect=AssertionError("offline-only"))
        self.send_guard.start()
        self.addCleanup(self.send_guard.stop)

    def test_only_three_outbound_patterns_change_and_strict_contract_is_preserved(self) -> None:
        before, after = legacy_provider_schema(), provider_schema()
        self.assertEqual(different_paths(before, after), set(PATTERN_PATHS))
        for path in PATTERN_PATHS:
            self.assertEqual(at_path(before, path), r"\S")
            self.assertEqual(at_path(after, path), r"^[\s\S]*\S[\s\S]*$")
        for name, definition in after["$defs"].items():
            if definition.get("type") == "object":
                with self.subTest(definition=name):
                    self.assertFalse(definition["additionalProperties"])
                    self.assertEqual(definition["required"], list(definition["properties"]))
        self.assertFalse(after["additionalProperties"])
        self.assertEqual(PROVIDER_SCHEMA_VERSION, "chat-intent-schema-v2-whole-text")
        self.assertEqual(PROMPT_VERSION, "chat-intent-v2-city-directory")

    def test_old_pattern_reproduces_search_versus_full_match_disagreement(self) -> None:
        for value in ("苏州站附近", "拙政园", "Suzhou Museum", "A B"):
            with self.subTest(value=value):
                self.assertEqual(TypeAdapter(ShortText).validate_python(value), value)
                self.assertIsNotNone(re.search(r"\S", value))
                self.assertIsNone(re.fullmatch(r"\S", value))
        self.assertIsNotNone(re.fullmatch(r"\S", "住"))

    def test_new_pattern_agrees_for_search_and_full_match_without_name_truncation(self) -> None:
        patterns = [at_path(provider_schema(), path) for path in PATTERN_PATHS]
        rust_adapters = {pattern: TypeAdapter(Annotated[str, StringConstraints(pattern=pattern)])
                         for pattern in patterns}
        for value in ("苏州站附近", "拙政园", "Suzhou Museum", "A B", "名称\n补充", "住", "州" * 120):
            for pattern in patterns:
                with self.subTest(value=value, pattern=pattern):
                    self.assertIsNotNone(re.search(pattern, value))
                    self.assertIsNotNone(re.fullmatch(pattern, value))
                    self.assertEqual(rust_adapters[pattern].validate_python(value), value)
                    self.assertEqual(TypeAdapter(ShortText).validate_python(value), value)
        for value in ("", " ", "\n\r\t", "\u3000\u2003"):
            for pattern in patterns:
                with self.subTest(value=repr(value), pattern=pattern):
                    self.assertIsNone(re.search(pattern, value))
                    self.assertIsNone(re.fullmatch(pattern, value))
                    with self.assertRaises(ValidationError):
                        rust_adapters[pattern].validate_python(value)
            with self.assertRaises(ValidationError):
                TypeAdapter(ShortText).validate_python(value)

    def test_short_controlled_alphabet_preserves_contains_nonblank_semantics(self) -> None:
        # Newline support matters: a plain dot-star replacement would silently
        # narrow existing legal multiline text. This is regex, not LLM testing.
        pattern = at_path(provider_schema(), PATTERN_PATHS[0])
        for length in range(4):
            for letters in itertools.product(("A", "中", " ", "\n", "\t", "\u3000"), repeat=length):
                value = "".join(letters)
                expected = re.search(r"\S", value) is not None
                self.assertEqual(re.search(pattern, value) is not None, expected, repr(value))
                self.assertEqual(re.fullmatch(pattern, value) is not None, expected, repr(value))

    def test_local_length_controls_duplicate_and_whitespace_rules_are_not_relaxed(self) -> None:
        for field in ("accommodation_hint", "required_place_hints", "preferences"):
            for value in ("州" * 121, "正常\x00文字", "正常\x1f文字", "正常\x7f文字"):
                data = {field: value if field == "accommodation_hint" else [value]}
                with self.subTest(field=field, value=repr(value)), self.assertRaises(ValidationError):
                    Intent.model_validate(data)
        for field in ("required_place_hints", "preferences"):
            for values in (["拙政园", "拙政园"], [" 拙政园"], ["拙政园 "], ["\t"]):
                with self.subTest(field=field, values=values), self.assertRaises(ValidationError):
                    Intent.model_validate({field: values})
        self.assertEqual(Intent(accommodation_hint=" 苏州站附近 ").accommodation_hint, " 苏州站附近 ")
        self.assertEqual(Intent(required_place_hints=["园"]).required_place_hints, ["园"])

    def test_repeated_builds_return_independent_schemas_without_mutating_local_model(self) -> None:
        local_before = Selection.model_json_schema()
        first, second = provider_schema(), provider_schema()
        self.assertEqual(first, second)
        first["$defs"]["Intent"]["properties"]["required_place_hints"]["items"]["pattern"] = "BROKEN"
        first["$defs"]["Intent"]["required"].clear()
        self.assertEqual(second, provider_schema())
        self.assertEqual(Selection.model_json_schema(), local_before)
        for path in PATTERN_PATHS:
            self.assertEqual(at_path(local_before, path), r"\S")

    def test_payload_changes_only_schema_not_prompt_context_parameters_or_tools(self) -> None:
        conversation = {"requirements": Intent().model_dump(),
                        "messages": [{"role": "user", "text": "想在苏州玩三天。"}]}
        original = copy.deepcopy(conversation)
        payload = model_payload(conversation, "住苏州站附近，按拙政园、苏州博物馆的顺序想去。",
                                model="qwen3.7-flash-2026-07-15",
                                now=datetime(2026, 10, 21, 8, tzinfo=timezone.utc))
        self.assertEqual(conversation, original)
        self.assertEqual(payload["response_format"]["json_schema"]["schema"], provider_schema())
        rest = copy.deepcopy(payload)
        rest["response_format"]["json_schema"].pop("schema")
        canonical = json.dumps(rest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(sha256(canonical).hexdigest(), PRE_FIX_PAYLOAD_WITHOUT_SCHEMA_SHA256)
        self.assertNotIn("tools", payload)
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["enable_thinking"])
        self.assertEqual(payload["max_tokens"], 1024)

    def test_old_failure_values_remain_structure_legal_but_do_not_answer_the_request(self) -> None:
        # Sanitised inline replay of the known bad field values, not a new model
        # output or a regrading of the immutable original acceptance evidence.
        user = ("苏州，2026年11月1日至3日，两个人，总预算3000元，住苏州站附近，"
                "按拙政园、苏州博物馆的顺序想去，交通步行。")
        values = Intent(destination_id="320500", start_date="2026-11-01", end_date="2026-11-03",
                        travelers=2, budget_cny="3000", accommodation_hint="住",
                        required_place_hints=["拙", "苏"], preferences=["按", "的", "去", "想", "步"],
                        transport_mode="walking").model_dump()
        selected = Selection.model_validate({"requirements": values,
            "sources": [{"field": field, "quote": user} for field in Intent.model_fields],
            "issues": []})
        result, _ = apply_selection(Intent(), selected, user)
        self.assertEqual(result.model_dump(), values)
        self.assertNotEqual(result.accommodation_hint, "苏州站附近")
        self.assertNotEqual(result.required_place_hints, ["拙政园", "苏州博物馆"])
        self.assertNotEqual(result.preferences, [])
        for path in PATTERN_PATHS:
            self.assertIsNotNone(re.fullmatch(at_path(provider_schema(), path), "住"))


if __name__ == "__main__":
    unittest.main()
