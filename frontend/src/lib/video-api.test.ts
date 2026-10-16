import { execFileSync } from "node:child_process";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { extractVideo, parseVideoResult } from "./video-api";

const valid={contract_version:"video-frames-v1",request_id:"r",duration_seconds:1,width:320,height:180,rotation_degrees:0,sample_count:1,
 frames:[{index:1,target_seconds:.5,width:320,height:180,jpeg_base64:btoa("\xff\xd8\xffcontrolled\xff\xd9")}]};
afterEach(()=>{vi.unstubAllGlobals();vi.useRealTimers();});
describe("bounded video client contract",()=>{
 it("accepts real FFmpeg → Python output without private input metadata",()=>{
   const backend=path.resolve(process.cwd(),"../backend");
   const output=execFileSync(path.join(backend,".venv/bin/python"),["-B","-c",`
import asyncio,tempfile,sys
from pathlib import Path
sys.path.insert(0,"tests")
from test_video import fixture
from app.services.video import extract_frames,executables
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/"private-name.mp4"
 fixture(p,duration="2")
 print(asyncio.run(extract_frames(p,"r",executables())).model_dump_json())
`],{cwd:backend,encoding:"utf8"});
   const result=parseVideoResult(JSON.parse(output),"r");expect(result.frames).toHaveLength(1);expect(output).not.toContain("private-name");
 });
 it.each([
   {...valid,request_id:"old"},{...valid,url:"https://example.test"},{...valid,duration_seconds:31},
   {...valid,width:1921},{...valid,width:1900,height:1900},{...valid,rotation_degrees:45},
   {...valid,sample_count:2},{...valid,frames:[]},{...valid,frames:[valid.frames[0],valid.frames[0]]},
   {...valid,frames:[{...valid.frames[0],target_seconds:0}]},{...valid,frames:[{...valid.frames[0],width:1281}]},
   {...valid,frames:[{...valid.frames[0],jpeg_base64:btoa("<svg>bad</svg>")}]},
 ])("rejects invalid response without repairing or truncating",v=>expect(()=>parseVideoResult(v,"r")).toThrow());
 it("sends one binary request, not a filename/URL; safe errors and no retry",async()=>{
   const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify(valid)));vi.stubGlobal("fetch",fetcher);
   const file=new File(["bytes"],"private.mp4");expect(await extractVideo(file,"r",new AbortController().signal)).toEqual(valid);
   expect(fetcher).toHaveBeenCalledOnce();expect(fetcher.mock.calls[0][0]).not.toContain("private");expect(fetcher.mock.calls[0][1].body).toBe(file);
   fetcher.mockResolvedValue(new Response(JSON.stringify({detail:{code:"unknown",message:"raw sensitive error"}}),{status:500}));
   await expect(extractVideo(file,"r",new AbortController().signal)).rejects.toThrow("视频处理未完成");expect(fetcher).toHaveBeenCalledTimes(2);
 });
 it("aborted before send uses zero calls; 60s timeout cancels once without retry",async()=>{
   vi.useFakeTimers();const fetcher=vi.fn((_url,options)=>new Promise((_resolve,reject)=>options.signal.addEventListener("abort",()=>reject(new DOMException("cancel","AbortError")))));vi.stubGlobal("fetch",fetcher);
   const c=new AbortController();c.abort();const f=new File(["x"],"x.mp4");
   await expect(extractVideo(f,"r",c.signal)).rejects.toThrow();expect(fetcher).not.toHaveBeenCalled();
   const promise=extractVideo(f,"r",new AbortController().signal);const assertion=expect(promise).rejects.toThrow("60 秒");
   await vi.advanceTimersByTimeAsync(60000);await assertion;expect(fetcher).toHaveBeenCalledOnce();
 });
 it("caps streamed response before parsing",async()=>{
   vi.stubGlobal("fetch",vi.fn().mockResolvedValue(new Response(new Uint8Array(5*1024*1024+1))));
   await expect(extractVideo(new File(["x"],"x.mp4"),"r",new AbortController().signal)).rejects.toThrow("5 MiB");
 });
});
