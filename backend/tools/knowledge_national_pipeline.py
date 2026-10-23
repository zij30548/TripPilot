"""Offline, review-first source preparation. Never downloads or calls a model.

Cleaning is not factual verification: a human must bind the resulting text to
an approved official source and curate complete thematic evidence units. Long
conditions are rejected for review rather than truncated or split by a model.
No original corpus, user database, runtime configuration or index is rewritten.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
import unicodedata

MAX_SOURCE_BYTES = 1024 * 1024
MAX_BLOCKS = 4000
MAX_BLOCK_CHARS = 20000
MAX_DEPTH = 128
SCHEMA = "knowledge-prepared-source-v1"
_SKIP = frozenset({"script", "style", "nav", "header", "footer", "aside", "noscript", "form", "svg", "canvas", "template"})
_BREAK = frozenset({"p", "div", "section", "article", "li", "tr", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"})
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})


class PreparationError(ValueError):
    """Fixed error codes only: no source body, private path or parser exception."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _plain(text: str) -> str:
    # NFC preserves numerals/comparators and wording. Never rewrite ages,
    # conjunctions, exceptions, dates or admission conclusions during cleanup.
    return re.sub(r"[\t\n\r \u00a0]+", " ", unicodedata.normalize("NFC", text)).strip()


@dataclass(frozen=True)
class PreparedSource:
    schema_version: str
    format: str
    source_sha256: str
    reviewed_text_sha256: str
    blocks: list[dict[str, str]]
    review_required: bool = True


class _ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []
        self.size = 0
        self.kind = "paragraph"
        self.blocks: list[dict[str, str]] = []

    def flush(self) -> None:
        text = _plain("".join(self.parts))
        self.parts, self.size = [], 0
        if text:
            if len(text) > MAX_BLOCK_CHARS or len(self.blocks) >= MAX_BLOCKS:
                raise PreparationError("source_review_capacity")
            self.blocks.append({"kind": self.kind, "text": text})
        self.kind = "paragraph"

    def suppressed(self) -> bool:
        return bool(self.stack and self.stack[-1][1])

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        style = re.sub(r"\s+", "", (values.get("style") or "").lower())
        skip = (self.suppressed() or tag in _SKIP or "hidden" in values
                or (values.get("aria-hidden") or "").lower() == "true"
                or "display:none" in style or "visibility:hidden" in style)
        if tag in _BREAK and not self.suppressed():
            self.flush()
            self.kind = "heading" if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} else "table_row" if tag == "tr" else "paragraph"
        if tag in {"br", "hr"} and not skip:
            self.parts.append("\n")
        if tag in {"td", "th"} and not skip:
            self.parts.append(" | ")
        if tag not in _VOID:
            if len(self.stack) >= MAX_DEPTH:
                raise PreparationError("source_review_capacity")
            self.stack.append((tag, skip))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        was_suppressed = self.suppressed()
        if tag in _BREAK and not was_suppressed:
            self.flush()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if not self.suppressed():
            self.size += len(data)
            if self.size > MAX_BLOCK_CHARS:
                raise PreparationError("source_review_capacity")
            self.parts.append(data)


def prepare_source(raw: bytes, *, format: str) -> PreparedSource:
    """Remove known page chrome; retain conditions for manual source review.

    This deliberately performs no factual extraction, OCR, JS rendering,
    translation, URL fetch or automatic conversion into the reviewed corpus.
    """
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_SOURCE_BYTES:
        raise PreparationError("source_size")
    if format not in {"html", "text", "markdown"}:
        raise PreparationError("source_format")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise PreparationError("source_encoding") from None
    if any(ord(char) < 32 and char not in "\t\n\r" or 0x7F <= ord(char) <= 0x9F for char in text):
        raise PreparationError("source_control_character")
    if format == "html":
        parser = _ReadableHTML()
        try:
            parser.feed(text)
            parser.close()
            parser.flush()
        except PreparationError:
            raise
        except Exception:
            raise PreparationError("source_html") from None
        blocks = parser.blocks
    else:
        blocks = []
        for paragraph in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
            cleaned = _plain(paragraph)
            if not cleaned:
                continue
            if len(cleaned) > MAX_BLOCK_CHARS or len(blocks) >= MAX_BLOCKS:
                raise PreparationError("source_review_capacity")
            blocks.append({"kind": "heading" if format == "markdown" and cleaned.startswith("#") else "paragraph", "text": cleaned})
    if not blocks:
        # A JS-only/blocked page is missing evidence, not an empty valid guide.
        raise PreparationError("source_no_readable_content")
    normalized = "\n\n".join(block["text"] for block in blocks).encode("utf-8")
    return PreparedSource(SCHEMA, format, _sha(raw), _sha(normalized), blocks)


def _read_regular(path: Path) -> bytes:
    if not path.is_absolute():
        raise PreparationError("absolute_source_path_required")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_SOURCE_BYTES:
                raise PreparationError("source_regular_file_required")
            raw = handle.read(MAX_SOURCE_BYTES + 1)
    except OSError:
        raise PreparationError("source_read_failed") from None
    return raw


def _write_exclusive(path: Path, value: object) -> None:
    if not path.is_absolute():
        raise PreparationError("absolute_output_path_required")
    raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    if len(raw) > 2 * MAX_SOURCE_BYTES:
        raise PreparationError("prepared_output_capacity")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        # Never overwrite an existing review artifact or delete uncertain work.
        raise PreparationError("exclusive_output_failed") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline official-source preparation; no fetching or model execution")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Clean a manually obtained public source into a NEW private review file")
    prepare.add_argument("source", type=Path)
    prepare.add_argument("--format", choices=("html", "text", "markdown"), required=True)
    prepare.add_argument("--output", type=Path, required=True)
    commands.add_parser("verify", help="Validate the reviewed national corpus/source bindings and rebuild an in-memory index")
    options = parser.parse_args(argv)
    try:
        if options.command == "prepare":
            result = prepare_source(_read_regular(options.source), format=options.format)
            _write_exclusive(options.output, asdict(result))
            print(json.dumps({"schema_version": SCHEMA, "blocks": len(result.blocks), "source_sha256": result.source_sha256,
                              "reviewed_text_sha256": result.reviewed_text_sha256, "review_required": True}))
        else:
            from tooling.travel.knowledge_national import build_national_index
            index = build_national_index()
            print(json.dumps({"schema_version": "knowledge-build-receipt-v2", "corpus_hash": index.hash,
                              "corpus_version": index.corpus.corpus_version, "documents": len(index.corpus.documents),
                              "venues": len(index.corpus.venues), "cities": len(index.corpus.cities),
                              "chunks": len(index.chunks), "model_calls": 0, "persistent_writes": False}))
        return 0
    except (PreparationError, ValueError, OSError):
        print(json.dumps({"status": "failed", "code": "offline_preparation_or_binding_failed", "model_calls": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
