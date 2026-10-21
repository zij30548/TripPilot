"""Rebuildable, deterministic in-memory Chinese BM25. Never makes network calls."""
import argparse
from collections import Counter
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata

from tooling.providers.qwen import strict_json
from core.schemas.knowledge import Corpus, KnowledgeRequest, KnowledgeResponse, Match, Source

DATA = Path(__file__).resolve().parents[2] / "knowledge"
METHOD = "cjk-bigram-bm25-v1"
# General lexical normalization, fixed before the held-out evaluation. Not rules
# deciding admission, campus identity, applicability or answers.
ALIASES = {"推车": "婴儿车", "带娃": "儿童", "开门": "开放", "营业": "开放", "订票": "购票", "人广": "人民广场馆"}
STOP = ["所有", "请问", "我想知道", "可不可以", "能不能", "有没有", "怎么样", "是什么", "需要", "可以", "怎么", "哪里", "多少", "几点", "什么时候", "上海博物馆", "上海自然博物馆", "人民广场馆", "自然博物馆", "上博东馆", "东馆", "自博", "上博", "自然馆", "整理摘要"]


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower()
    for old, new in ALIASES.items():
        text = text.replace(old, new)
    for stop in STOP:
        text = text.replace(stop, " ")
    result: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", text):
        if re.fullmatch(r"[\u4e00-\u9fff]+", run):
            result.extend(run[i:i + 2] for i in range(len(run) - 1))
        else:
            result.append(run)
    return result


class KnowledgeIndex:
    def __init__(self, corpus: Corpus):
        self.corpus = corpus
        self.hash = fingerprint(corpus.model_dump(mode="json"))
        self.chunks: list[tuple[Match, Counter[str]]] = []
        for doc in corpus.documents:
            raw = doc.model_dump(mode="json")
            raw.pop("sections")
            source = Source.model_validate_json(json.dumps(raw))
            digest = fingerprint(doc.model_dump(mode="json"))
            # Each curated thematic section is atomic: never split exceptions,
            # age/date constraints or contradictory clauses into separate hits.
            for section in doc.sections:
                item = Match(chunk_id=f"{doc.id}/{section.id}", content_hash=digest,
                             section=section, source=source, score=1.0, matched_terms=["init"])
                self.chunks.append((item, Counter(tokens(section.heading + " " + section.summary + " " + section.excerpt))))

    def search(self, request: KnowledgeRequest) -> KnowledgeResponse:
        rows = [(m, terms) for m, terms in self.chunks if m.source.venue_id == request.venue_id]
        query = set(tokens(request.question))
        avg = sum(sum(t.values()) for _, t in rows) / len(rows)
        frequencies = Counter(term for _, terms in rows for term in terms)
        hits: list[Match] = []
        for match, terms in rows:
            common = sorted(query & terms.keys())
            if not common:
                continue
            score = sum(math.log(1 + (len(rows) - frequencies[t] + 0.5) / (frequencies[t] + 0.5)) *
                        terms[t] * 2.2 / (terms[t] + 1.2 * (0.25 + 0.75 * sum(terms.values()) / avg)) for t in common)
            hits.append(match.model_copy(update={"score": round(score, 6), "matched_terms": common[:24]}))
        hits.sort(key=lambda m: (-m.score, m.chunk_id))
        return KnowledgeResponse(corpus_version=self.corpus.corpus_version, corpus_hash=self.hash,
            document_count=len(self.corpus.documents), chunk_count=len(self.chunks), request=request,
            venue=next(v for v in self.corpus.venues if v.id == request.venue_id),
            status="matches" if hits else "no_match", matches=hits[:request.limit])


def build_index(path: Path = DATA / "corpus.json") -> KnowledgeIndex:
    raw = path.read_bytes()
    if len(raw) > 128 * 1024:
        raise ValueError("Corpus exceeds reviewed capacity")
    strict_json(raw)
    return KnowledgeIndex(Corpus.model_validate_json(raw))


@lru_cache(maxsize=1)
def get_index() -> KnowledgeIndex:
    return build_index()


def evaluate(index: KnowledgeIndex, split: str) -> dict:
    fixture = strict_json((DATA / "evaluation.json").read_bytes())
    if fixture["corpus_version"] != index.corpus.corpus_version:
        raise ValueError("Evaluation version mismatch")
    rows = []
    for case in fixture["cases"]:
        if case["split"] != split:
            continue
        if any(ref not in {m.chunk_id for m, _ in index.chunks} for ref in case["relevant"]):
            raise ValueError("Invalid annotation")
        response = index.search(KnowledgeRequest(venue_id=case["venue_id"], question=case["question"], limit=5))
        ids = [m.chunk_id for m in response.matches]
        rank = next((i + 1 for i, id in enumerate(ids) if id in case["relevant"]), None)
        rows.append({**case, "returned": ids, "first_relevant_rank": rank,
                     "campus_errors": sum(m.source.venue_id != case["venue_id"] for m in response.matches)})
    relevant = [r for r in rows if r["relevant"]]
    absent = [r for r in rows if not r["relevant"]]
    return {"method": METHOD, "corpus_version": index.corpus.corpus_version, "corpus_hash": index.hash,
            "split": split, "questions": len(rows),
            "hit_at_3": {"numerator": sum(r["first_relevant_rank"] is not None and r["first_relevant_rank"] <= 3 for r in relevant), "denominator": len(relevant)},
            "first_rank_counts": dict(Counter(str(r["first_relevant_rank"]) for r in relevant)),
            "no_material_false_hits": {"numerator": sum(bool(r["returned"]) for r in absent), "denominator": len(absent)},
            "hit_at_3_by_expectation": {kind: {"numerator": sum(r["first_relevant_rank"] is not None and r["first_relevant_rank"] <= 3 for r in relevant if r["expectation"] == kind),
                                                "denominator": sum(r["expectation"] == kind for r in relevant)} for kind in ["evidence", "limited"]},
            "campus_errors": sum(r["campus_errors"] for r in rows), "cases": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Offline corpus validation, repeatable index build and frozen evaluation")
    parser.add_argument("--evaluate", choices=["dev", "validation"])
    args = parser.parse_args()
    index = build_index()
    report = evaluate(index, args.evaluate) if args.evaluate else {
        "method": METHOD, "corpus_version": index.corpus.corpus_version, "corpus_hash": index.hash,
        "documents": len(index.corpus.documents), "chunks": len(index.chunks),
        "sources": [{"id": d.id, "url": d.url, "hash": fingerprint(d.model_dump(mode="json"))} for d in index.corpus.documents]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
