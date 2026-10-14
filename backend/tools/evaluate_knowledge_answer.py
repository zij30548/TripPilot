"""One chosen frozen case → one explicit HTTP answer operation. No loop/retry."""
import argparse
import json
from pathlib import Path
import uuid
import httpx


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case")
    args = parser.parse_args()
    cases = json.loads((Path(__file__).resolve().parents[1]/"knowledge/answer-evaluation.json").read_text())["cases"]
    case = next(c for c in cases if c["id"] == args.case)
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as client:
        retrieval = client.post("/knowledge/search", json={"venue_id":case["venue_id"], "question":case["question"], "limit":5})
        retrieval.raise_for_status(); data = retrieval.json()
        result = client.post("/knowledge/answer", json={"venue_id":case["venue_id"], "question":case["question"],
            "request_id":f"6d2-{case['id']}-{uuid.uuid4().hex}", "corpus_version":data["corpus_version"], "corpus_hash":data["corpus_hash"]})
        value = result.json()
        print(json.dumps({"case":case["id"], "http_status":result.status_code,
            "response":{k:value.get(k) for k in ["status","answers","missing","citations","origin","usage","detail"]}},ensure_ascii=False))
