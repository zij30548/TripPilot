import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { useDialoguePlaces } from "@/lib/dialogue-places-controller";
import { placesConversation, placesFixture, batchFixture, placeFixture, succeeded } from "@/lib/chat-places-test-fixtures";
import { deferred } from "@/lib/chat-test-fixtures";
import type { PhotoResult } from "@/lib/photo-api";
import type { VideoResult } from "@/lib/video-api";
import DialogueMedia from "./dialogue-media";

const mocks = vi.hoisted(() => ({ photo: vi.fn(), video: vi.fn(), legacySearch: vi.fn() }));
vi.mock("@/lib/photo-api", async original => ({ ...await original<typeof import("@/lib/photo-api")>(), recognizePhoto: mocks.photo }));
vi.mock("@/lib/video-api", async original => ({ ...await original<typeof import("@/lib/video-api")>(), extractVideo: mocks.video }));
vi.mock("@/lib/places-api", () => ({ searchPlaces: mocks.legacySearch }));
const clue = (city: string | null = "Shanghai"): PhotoResult => ({ contract_version: "photo-landmark-v2", request_id: "controlled", destination_id: "shanghai", status: "possible", clues: [{ name: "受控地标", city }], model: "qwen3-vl-plus", usage: { input_tokens: 1, output_tokens: 1 } });
const poi = { ...placeFixture(), name: "受控地标", region: { cityname: "上海市", citycode: "021", adcode: "310101" } };
const photo = () => new File(["controlled"], "fixture.png", { type: "image/png" });
const wire = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
function setup(mode: "photo" | "video" = "photo", full = false) {
  let server = placesFixture({ city_id: "310000", required_places: full ? Array.from({ length: 6 }, (_, i) => ({ ...poi, id: `p${i}` })) : [] });
  const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const command = JSON.parse(String(init.body));
      server = { ...server, revision: server.revision + 1, last_operation: succeeded(command.operation_id),
        ...(command.action === "search" ? { batch: { ...batchFixture([poi]), city_id: "310000" } } : {}),
        ...(command.action === "confirm" ? { required_places: [poi] } : {}) };
    }
    return wire(server);
  }); vi.stubGlobal("fetch", fetcher);
  const close = vi.fn(), result = vi.fn();
  function Harness({ session = "a" }: { session?: string }) {
    const conversation = placesConversation(); conversation.requirements.destination_id = "310000";
    const places = useDialoguePlaces(conversation);
    return <><button onClick={() => void places.read()}>读取测试副本</button>{places.state.workspace && <DialogueMedia mode={mode} sessionKey={session}
      destinationId="shanghai" places={places} onClose={close} onResult={result} />}</>;
  }
  const rendered = render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "读取测试副本" }));
  return { ...rendered, Harness, fetcher, close, result };
}
async function recognize() {
  fireEvent.change(await screen.findByLabelText(/选择一张照片/), { target: { files: [photo()] } });
  fireEvent.click(screen.getByRole("button", { name: "识别照片" }));
  await screen.findByText("可能地点：受控地标");
}
beforeEach(() => {
  vi.clearAllMocks(); let n = 0; URL.createObjectURL = vi.fn(() => `blob:controlled-${++n}`); URL.revokeObjectURL = vi.fn();
  mocks.photo.mockResolvedValue(clue());
  mocks.video.mockResolvedValue({ contract_version: "video-frames-v1", request_id: "v", duration_seconds: 8, width: 320, height: 180, rotation_degrees: 0, sample_count: 2,
    frames: [1, 2].map(index => ({ index, target_seconds: index * 4 - 2, width: 320, height: 180, jpeg_base64: btoa(`controlled-frame-${index}`) })) } satisfies VideoResult);
});
describe("media attachment reuses dialogue POI confirmation", () => {
  it("selection is local, explicit search uses persisted batch, preview and confirmation are separate", async () => {
    const v = setup(); const input = await screen.findByLabelText(/选择一张照片/);
    fireEvent.change(input, { target: { files: [photo()] } }); expect(mocks.photo).not.toHaveBeenCalled(); expect(v.fetcher).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "识别照片" })); await screen.findByText("可能地点：受控地标");
    expect(v.result).not.toHaveBeenCalled(); expect(v.fetcher).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "用此线索搜索高德" })); await screen.findByRole("button", { name: "预览地点1" });
    expect(mocks.legacySearch).not.toHaveBeenCalled(); expect(screen.queryByRole("button", { name: "确认加入必去" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "预览地点1" })); expect(v.fetcher).toHaveBeenCalledTimes(2); expect(v.result).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认加入必去" })); await waitFor(() => expect(v.result).toHaveBeenCalledOnce());
    expect(v.close).toHaveBeenCalledOnce(); expect(JSON.parse(String(v.fetcher.mock.calls[2][1]?.body))).toMatchObject({ action: "confirm", place_id: poi.id, batch_id: batchFixture().id });
    expect(JSON.parse(String(v.fetcher.mock.calls[2][1]?.body))).not.toHaveProperty("place");
  });
  it.each(["Beijing", null, "London"])("does not search or confirm a %s clue", async city => {
    mocks.photo.mockResolvedValue(clue(city)); const v = setup(); await recognize();
    expect(screen.getByRole("button", { name: "用此线索搜索高德" }).hasAttribute("disabled")).toBe(true);
    expect(v.fetcher).toHaveBeenCalledTimes(1); expect(v.result).not.toHaveBeenCalled();
  });
  it("full required list blocks recognition; unrecognized stays non-actionable", async () => {
    const v = setup("photo", true); fireEvent.change(await screen.findByLabelText(/选择一张照片/), { target: { files: [photo()] } });
    expect(screen.getByRole("button", { name: "识别照片" }).hasAttribute("disabled")).toBe(true); expect(mocks.photo).not.toHaveBeenCalled(); expect(v.result).not.toHaveBeenCalled();
  });
  it("changing a photo invalidates an already returned candidate and releases its URL", async () => {
    const v = setup(); await recognize(); fireEvent.click(screen.getByRole("button", { name: "用此线索搜索高德" })); await screen.findByRole("button", { name: "预览地点1" });
    fireEvent.click(screen.getByRole("button", { name: "预览地点1" }));
    fireEvent.change(screen.getByLabelText(/选择一张照片/), { target: { files: [photo()] } });
    expect(screen.queryByRole("button", { name: "确认加入必去" })).toBeNull(); expect(screen.queryByRole("button", { name: "预览地点1" })).toBeNull();
    expect(v.result).not.toHaveBeenCalled(); expect(URL.revokeObjectURL).toHaveBeenCalled();
  });
  it("Escape closes attachment and cancels recognition without altering confirmed places", async () => {
    const late = deferred<PhotoResult>(); mocks.photo.mockReturnValue(late.promise); const v = setup();
    fireEvent.change(await screen.findByLabelText(/选择一张照片/), { target: { files: [photo()] } });
    fireEvent.click(screen.getByRole("button", { name: "识别照片" }));
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(v.close).toHaveBeenCalledOnce(); expect(mocks.photo.mock.calls[0][3].aborted).toBe(true);
    await act(async () => { late.resolve(clue()); });
    expect(v.result).not.toHaveBeenCalled(); expect(v.fetcher).toHaveBeenCalledTimes(1); expect(URL.revokeObjectURL).toHaveBeenCalled();
  });
  it.each(["success", "error"])("attachment A→B→A isolates old %s and finally", async kind => {
    const late = deferred<PhotoResult>(); mocks.photo.mockReturnValue(late.promise); const v = setup();
    fireEvent.change(await screen.findByLabelText(/选择一张照片/), { target: { files: [photo()] } }); fireEvent.click(screen.getByRole("button", { name: "识别照片" }));
    v.rerender(<v.Harness session="b" />); v.rerender(<v.Harness session="a" />);
    await act(async () => { if (kind === "success") late.resolve(clue()); else late.reject(new Error("old-image-error")); });
    expect(screen.queryByText("可能地点：受控地标")).toBeNull(); expect(screen.queryByText("old-image-error")).toBeNull(); expect(v.result).not.toHaveBeenCalled();
    expect(mocks.photo.mock.calls[0][3].aborted).toBe(true);
  });
  it("video selection sends only the selected frame on explicit recognition; changing frames invalidates it", async () => {
    const v = setup("video"); fireEvent.change(await screen.findByLabelText(/选择一个短视频/), { target: { files: [new File(["v"], "sample.mp4", { type: "video/mp4" })] } });
    expect(mocks.video).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole("button", { name: "提取画面" }));
    fireEvent.click(await screen.findByRole("button", { name: "放大画面2，约6.00秒" })); expect(mocks.photo).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "识别选中画面" })); await screen.findByText("可能地点：受控地标");
    const sent = mocks.photo.mock.calls[0][0] as File; expect(sent.name).toBe("frame.jpg"); expect(sent.type).toBe("image/jpeg"); expect(sent.size).toBe("controlled-frame-2".length);
    fireEvent.click(screen.getByRole("button", { name: "用此线索搜索高德" })); await screen.findByRole("button", { name: "预览地点1" });
    fireEvent.click(screen.getByRole("button", { name: "放大画面1，约2.00秒" }));
    expect(screen.queryByRole("button", { name: "预览地点1" })).toBeNull(); expect(v.result).not.toHaveBeenCalled(); expect(mocks.photo).toHaveBeenCalledOnce();
  });
});
