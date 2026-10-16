import { test } from "node:test";
import assert from "node:assert/strict";
import { runInNewContext } from "node:vm";
import { bindReplay, installPhotoCityReplay, loadReplay } from "./photo-city-replay.mjs";

test("rebind only identity; preserve historical status/name/city and source object", () => {
  // Controlled fixture; the browser run separately verifies actual saved-file hashes.
  const old = { status: "possible", clues: [{ name: "Example", city: "Shanghai" }], request_id: "old", destination_id: "shanghai" };
  const copy = structuredClone(old);
  const next = bindReplay(old, "http://127.0.0.1:8000/media/landmark?request_id=current&destination_id=shanghai");
  assert.deepEqual(old, copy); assert.equal(next.request_id, "current"); assert.deepEqual(next.clues, old.clues);
  assert.notEqual(next.clues, old.clues); assert.equal(next.status, old.status);
  for (const query of ["request_id=current&destination_id=beijing", "destination_id=shanghai", "request_id=x&destination_id=shanghai&extra=1"])
    assert.throws(() => bindReplay(old, `http://127.0.0.1:8000/media/landmark?${query}`));
  assert.throws(() => loadReplay("../../other"));
});

test("browser adapter rejects duplicate installation before reading or registering", async () => {
  await assert.rejects(installPhotoCityReplay({__photoCityReplay: {}}), /already installed/);
});

test("acceptance route fails closed: two replays only, other AI/save blocked, explicit POI remains real", async () => {
  let handler;
  const page = {route: async (_pattern, h) => {handler=h;}, on: () => {}, evaluate: async () => {}};
  // Exercise the actual adapter with in-memory fixtures; no filesystem/network.
  const install = runInNewContext(`(${installPhotoCityReplay.toString()})`, {
    loadReplay: () => ({response:{status:"possible",clues:[{name:"Example",city:"Shanghai"}]},sha256:"controlled"}),
    bindReplay, URL,
  });
  await install(page);
  async function request(path, method="POST") {
    const outcome={};
    await handler({request:()=>({url:()=>`http://127.0.0.1:8000${path}`,method:()=>method}),
      fulfill:async v=>{outcome.reply=v;},abort:async()=>{outcome.blocked=true;},continue:async()=>{outcome.live=true;}});
    return outcome;
  }
  for(const id of ["first","second"]) {
    const result=await request(`/media/landmark?request_id=${id}&destination_id=shanghai`);
    assert.equal(JSON.parse(result.reply.body).request_id,id);assert.equal(result.live,undefined);
  }
  assert.equal((await request("/media/landmark?request_id=third&destination_id=shanghai")).blocked,true);
  for(const path of ["/trips/schedule-explanation","/trips/itinerary-query","/knowledge/answer","/saved-trips"])
    assert.equal((await request(path)).blocked,true);
  assert.equal((await request("/places/search?keyword=Example&destination_id=shanghai","GET")).live,true);
  assert.equal(page.__photoCityReplay.replayCount,2);
});
