import { execFileSync } from "node:child_process";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { parsePhotoResult } from "./photo-api";

const valid = { contract_version: "photo-landmark-v2", request_id: "test", destination_id: "shanghai",
  status: "possible", clues: [{ name: "东方明珠", city: "上海" }], model: "qwen3-vl-plus",
  usage: { input_tokens: null, output_tokens: null } };

describe("conservative photo contract", () => {
  it("accepts the actual Python response, including unknown city and empty refusal", () => {
    const backend = path.resolve(process.cwd(), "../backend");
    const output = execFileSync(path.join(backend, ".venv/bin/python"), ["-B", "-c", `
import json
from app.schemas.photo import PhotoResponse
from app.schemas.explanation import ExplanationUsage
print(json.dumps([PhotoResponse(request_id="test", destination_id="shanghai", model="qwen3-vl-plus",
    usage=ExplanationUsage(input_tokens=None, output_tokens=None), status=status, clues=clues).model_dump()
    for status, clues in [("possible", [{"name":"东方明珠", "city":None}]), ("uncertain", []), ("unrecognized", [])]], ensure_ascii=False))
`], { cwd: backend, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
    for (const response of JSON.parse(output)) expect(parsePhotoResult(response, "test", "shanghai")).toEqual(response);
  });
  it.each([
    { ...valid, contract_version: "photo-landmark-v1" },
    { ...valid, clues: [] },
    { ...valid, clues: [...valid.clues, ...valid.clues] },
    { ...valid, status: "uncertain" },
    { ...valid, status: "unrecognized" },
    { ...valid, clues: [{ ...valid.clues[0], basis: "untrusted explanation" }] },
    { ...valid, clues: [{ ...valid.clues[0], poi_id: "fake" }] },
    { ...valid, clues: [{ name: "x", city: 1 }] },
    { ...valid, clues: [{ name: "x".repeat(81), city: null }] },
    { ...valid, destination_id: "beijing" },
    { ...valid, request_id: "old" },
  ])("rejects invalid structure without stripping or choosing the first clue", response => {
    expect(() => parsePhotoResult(response, "test", "shanghai")).toThrow();
  });
});
