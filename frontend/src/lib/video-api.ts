import { API_ENDPOINTS } from "./api-endpoints";
import { apiFetch } from "./auth-session";

/** Preview-only contract. No photo/model/POI client is imported here. */
export const VIDEO_BYTES = 20 * 1024 * 1024;
export const VIDEO_RESPONSE_BYTES = 5 * 1024 * 1024;
export type VideoFrame = { index: number; target_seconds: number; width: number; height: number; jpeg_base64: string };
export type VideoResult = { contract_version: "video-frames-v1"; request_id: string; duration_seconds: number;
  width: number; height: number; rotation_degrees: 0 | 90 | 180 | 270; sample_count: number; frames: VideoFrame[] };
const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v,k));
const int = (v: unknown, max: number): v is number => typeof v === "number" && Number.isInteger(v) && v > 0 && v <= max;
const number = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function parseVideoResult(value: unknown, requestId: string): VideoResult {
  if (!record(value) || !exact(value,["contract_version","request_id","duration_seconds","width","height","rotation_degrees","sample_count","frames"]) ||
      value.contract_version !== "video-frames-v1" || value.request_id !== requestId || !number(value.duration_seconds) || value.duration_seconds <= 0 || value.duration_seconds > 30 ||
      !int(value.width,1920) || !int(value.height,1920) || value.width*value.height > 2073600 || ![0,90,180,270].includes(value.rotation_degrees as number) ||
      !int(value.sample_count,6) || value.sample_count !== Math.min(6,Math.ceil(value.duration_seconds/5)) || !Array.isArray(value.frames) || !value.frames.length || value.frames.length > value.sample_count)
    throw new Error("视频画面响应未通过校验，未展示。");
  let previous = 0;
  for (const f of value.frames) {
    if (!record(f) || !exact(f,["index","target_seconds","width","height","jpeg_base64"]) || !int(f.index,value.sample_count) || f.index <= previous ||
        !number(f.target_seconds) || Math.abs(f.target_seconds-(f.index-.5)*value.duration_seconds/value.sample_count) > .000001 ||
        !int(f.width,1280) || !int(f.height,1280) || typeof f.jpeg_base64 !== "string" || f.jpeg_base64.length > 699052 ||
        !/^[A-Za-z0-9+/]+={0,2}$/.test(f.jpeg_base64) || f.jpeg_base64.length % 4)
      throw new Error("视频画面响应未通过校验，未展示。");
    const bytes = atob(f.jpeg_base64);
    if (bytes.length > 512*1024 || !bytes.startsWith("\xff\xd8\xff") || !bytes.endsWith("\xff\xd9")) throw new Error("画面图片格式无效，未展示。");
    previous = f.index;
  }
  return value as VideoResult;
}

const errors: Record<string,string> = {
  video_busy:"另一个视频仍在处理，请稍后主动重试。", video_unavailable:"视频处理依赖暂不可用，其他规划功能仍可使用。",
  upload_size:"请选择非空且不超过 20 MiB 的视频。", invalid_video:"无法读取有效的单视频流 MP4 / H.264；请检查格式、时长及尺寸。",
  external_reference:"不支持包含外部媒体引用的视频。", upload_timeout:"视频上传超过 30 秒，请主动重试。",
  processing_timeout:"视频处理超过 20 秒截止，已停止抽帧；清理可能需要少量额外时间。",
  output_limit:"画面输出超过安全上限，未截断为成功结果。", no_frames:"未取得可用画面，没有复制图片凑数。",
};

export async function extractVideo(file: File, requestId: string, signal: AbortSignal): Promise<VideoResult> {
  if (!/^[a-zA-Z0-9_-]{1,80}$/.test(requestId) || !file.size || file.size > VIDEO_BYTES) throw new Error("视频文件或请求身份无效。");
  if (signal.aborted) throw new DOMException("取消", "AbortError");
  const controller = new AbortController(); let timedOut = false;
  const cancel = () => controller.abort(); signal.addEventListener("abort",cancel,{once:true});
  const timer=setTimeout(()=>{timedOut=true;controller.abort();},60000);
  try {
    const response = await apiFetch(`${API_ENDPOINTS.video}?request_id=${encodeURIComponent(requestId)}`,
      {method:"POST",headers:{"Content-Type":"application/octet-stream"},body:file,signal:controller.signal});
    if (!response.body) throw new Error("视频处理响应为空。");
    const reader=response.body.getReader(); const parts:Uint8Array[]=[]; let size=0;
    try {
      while (true) { const {done,value}=await reader.read(); if(done)break; size+=value.byteLength;
        if(size>VIDEO_RESPONSE_BYTES) throw new Error("视频画面响应超过 5 MiB，未展示。"); parts.push(value); }
    } finally { await reader.cancel(); reader.releaseLock(); }
    if (controller.signal.aborted) throw new DOMException("取消", "AbortError");
    const data:unknown=JSON.parse(new TextDecoder().decode(join(parts,size)));
    if (!response.ok) throw new Error(record(data)&&record(data.detail)?errors[String(data.detail.code)]??"视频处理未完成，请主动重试。":"视频处理未完成，请主动重试。");
    return parseVideoResult(data,requestId);
  } catch(e) {
    if(signal.aborted) throw new DOMException("取消", "AbortError");
    if(timedOut) throw new Error("等待视频处理超过 60 秒，已取消等待；服务端会停止子进程并清理。");
    if(e instanceof Error && e.name === "Error") throw e;
    throw new Error("视频处理未完成，请检查本地服务后主动重试。");
  } finally { clearTimeout(timer); signal.removeEventListener("abort",cancel); }
}
function join(parts: Uint8Array[], size: number): Uint8Array {
  const data=new Uint8Array(size); let at=0; for(const p of parts){data.set(p,at);at+=p.length;} return data;
}
