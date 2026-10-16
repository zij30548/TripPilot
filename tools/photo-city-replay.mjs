// Acceptance tooling only. Not imported by the application or run by its server.
// Historical semantics stay unchanged; only request identity is rebound.
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";

const directory = "/Users/zijing/projects/trippilot-evidence/media-1/conservative-validation";
const hashes = {
  api01: "c777d9b9e0a94fa65e7ff4593a43ed1ab98a9ab3ffac83454a17070d51580412",
  api05: "fbeeabc7f7a51050269b5154b20308d96e2b7d232da534c1aebb331a1126d349",
};
export function loadReplay(id) {
  if (!Object.hasOwn(hashes, id)) throw new Error("Unapproved replay fixture");
  const bytes = readFileSync(`${directory}/${id}.json`);
  if (createHash("sha256").update(bytes).digest("hex") !== hashes[id]) throw new Error("Historical fixture changed");
  const fixture = JSON.parse(bytes);
  if (fixture.id !== id || fixture.http_status !== 200) throw new Error("Invalid replay fixture");
  return { response: fixture.response, sha256: hashes[id] };
}
export function bindReplay(response, url) {
  const u = new URL(url), id = u.searchParams.get("request_id");
  if (u.origin !== "http://127.0.0.1:8000" || u.pathname !== "/media/landmark" ||
      u.searchParams.get("destination_id") !== "shanghai" || !/^[a-zA-Z0-9-]{1,80}$/.test(id ?? "") ||
      [...u.searchParams.keys()].length !== 2) throw new Error("Unexpected replay request identity");
  return { ...structuredClone(response), request_id: id, destination_id: "shanghai" };
}

export async function installPhotoCityReplay(page) {
  if (page.__photoCityReplay) throw new Error("Replay already installed");
  const fixtures = ["api01", "api05"].map(id => ({ id, ...loadReplay(id) }));
  const audit = { label: "历史模型结果回放；真实高德查询另计；模型调用0", events: [], replayCount: 0, blocked: 0 };
  page.__photoCityReplay = audit;
  await page.route("http://127.0.0.1:8000/**", async route => {
    const request = route.request(), u = new URL(request.url()), path = u.pathname;
    if (path === "/media/landmark") {
      const fixture = fixtures[audit.replayCount];
      try {
        if (request.method() !== "POST" || !fixture) throw new Error("Unexpected replay operation");
        const response = bindReplay(fixture.response, request.url());
        audit.replayCount++;
        audit.events.push({ kind: "historical_replay", fixture: fixture.id, sha256: fixture.sha256,
          request_id: response.request_id, destination_id: response.destination_id, status: response.status, clues: response.clues });
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response) });
      } catch {
        audit.blocked++; audit.events.push({ kind: "blocked_replay", path });
        await route.abort("blockedbyclient");
      }
      return;
    }
    // Deny every other write/model endpoint, including saves. No fallback to a live model.
    if ((request.method() === "GET" && path === "/places/search") ||
        (request.method() === "POST" && path === "/trips/schedule-preview") ||
        (request.method() === "GET" && path.startsWith("/_AMapService/"))) {
      await route.continue(); return;
    }
    audit.blocked++; audit.events.push({ kind: "blocked_endpoint", path });
    await route.abort("blockedbyclient");
  });
  page.on("response", async response => {
    const u = new URL(response.url());
    if (u.origin !== "http://127.0.0.1:8000" || !["/places/search", "/trips/schedule-preview"].includes(u.pathname)) return;
    const event = { kind: "real_business", path: u.pathname, status: response.status() };
    if (u.pathname === "/places/search") event.keyword = u.searchParams.get("keyword");
    audit.events.push(event);
    if (response.ok()) {
      const data = await response.json();
      if (u.pathname === "/places/search") event.places = data.map(p => ({ id: p.id, name: p.name, address: p.address, region: p.region }));
      else audit.schedule = data;
    }
  });
  await page.evaluate(() => {
    const banner = document.createElement("p"); banner.id = "acceptance-replay-label";
    banner.textContent = "验收：历史模型结果回放（非实时识别）｜高德搜索为真实查询｜模型调用0";
    Object.assign(banner.style, { position: "fixed", top: "0", left: "0", right: "0", zIndex: "999999", margin: "0", padding: "6px", background: "#fff3cd", color: "#382c00", fontSize: "12px", pointerEvents: "none" });
    document.body.append(banner);
  });
  return { label: audit.label, fixtures: fixtures.map(f => ({ id: f.id, sha256: f.sha256 })) };
}

// Playwright MCP's sandbox cannot import modules. Produce a standalone snippet
// from these same functions and hash-checked fixtures, never a production route.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.length !== 3) throw new Error("Usage: node tools/photo-city-replay.mjs <new-snippet-file>");
  const fixtures = Object.fromEntries(["api01", "api05"].map(id => [id, loadReplay(id)]));
  const script = `async (page) => { const fixtures = ${JSON.stringify(fixtures)}; const loadReplay = id => structuredClone(fixtures[id]); const bindReplay = ${bindReplay.toString()}; return (${installPhotoCityReplay.toString()})(page); }`;
  writeFileSync(process.argv[2], script, { flag: "wx" });
  console.log("Prepared hash-checked, model-blocked historical replay snippet.");
}
