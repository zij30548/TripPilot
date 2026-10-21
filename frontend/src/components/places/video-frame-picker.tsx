"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { extractVideo, VIDEO_BYTES, type VideoResult, type VideoFrame } from "@/lib/video-api";
import type { DestinationId } from "@/lib/destinations";
import type { Place } from "@/types/place";
import PhotoPlacePicker, { type PreparedFrame, type PhotoClueSearch } from "./photo-place-picker";

const button="min-h-11 rounded-lg border px-4 py-2 text-sm disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2";
type Frame = VideoFrame & { url: string };
type Frames = { data: VideoResult; images: Frame[] };
type Recognition = { source: PreparedFrame; controller: AbortController; version: number; city: DestinationId };
export default function VideoFramePicker({sessionSignal,onClose,destinationId,excludedIds=[],onConfirm,onSearchClue}: {sessionSignal:AbortSignal; onClose:()=>void;
  destinationId?:DestinationId; excludedIds?:string[]; onConfirm?:(place:Place)=>void; onSearchClue?:(source:PhotoClueSearch)=>void}) {
  const [file,setFile]=useState<{file:File;url:string}|null>(null);
  const [frames,setFrames]=useState<Frames|null>(null);
  const [selected,setSelected]=useState<number|null>(null);
  const [loading,setLoading]=useState(false); const [error,setError]=useState("");
  const current=useRef<typeof file>(null); const published=useRef<Frames|null>(null);
  const active=useRef<AbortController|null>(null); const epoch=useRef(0); const alive=useRef(true);
  const section=useRef<HTMLElement>(null); const large=useRef<HTMLDivElement>(null);
  const [recognition,setRecognition]=useState<Recognition|null>(null);
  const recognitionRef=useRef<Recognition|null>(null); const recognitionVersion=useRef(0);
  const rendered=epoch.current;
  const validRender=()=>alive.current&&!sessionSignal.aborted&&epoch.current===rendered;
  const stopRecognition=useCallback(()=>{recognitionVersion.current++;recognitionRef.current?.controller.abort();recognitionRef.current=null;},[]);
  const invalidate=useCallback(()=>{epoch.current++;active.current?.abort();active.current=null;stopRecognition();},[stopRecognition]);
  function releaseFrames(){published.current?.images.forEach(f=>URL.revokeObjectURL(f.url));published.current=null;}
  function releaseFile(){if(current.current)URL.revokeObjectURL(current.current.url);current.current=null;}
  useEffect(()=>{
    alive.current=true;section.current?.focus();
    const stop=()=>{invalidate();releaseFrames();releaseFile();};
    sessionSignal.addEventListener("abort",stop,{once:true});
    return()=>{alive.current=false;stop();sessionSignal.removeEventListener("abort",stop);};
  },[sessionSignal,invalidate]);
  function choose(next:File|undefined){
    if(!alive.current||sessionSignal.aborted)return;
    invalidate();releaseFrames();releaseFile();setRecognition(null);setFile(null);setFrames(null);setSelected(null);setError("");setLoading(false);
    if(!next)return;
    if(!next.size||next.size>VIDEO_BYTES||!next.name.toLowerCase().endsWith(".mp4")){
      setError("请选择非空且不超过 20 MiB 的本地 MP4；服务端还会检查真实格式与编码。");return;
    }
    current.current={file:next,url:URL.createObjectURL(next)};setFile(current.current);
  }
  async function extract(){
    if(!validRender()||!current.current||active.current)return;
    invalidate();releaseFrames();const version=epoch.current, controller=new AbortController();active.current=controller;
    setRecognition(null);setFrames(null);setSelected(null);setError("");setLoading(true);
    const valid=()=>alive.current&&!sessionSignal.aborted&&!controller.signal.aborted&&epoch.current===version;
    try{
      const data=await extractVideo(current.current.file,crypto.randomUUID(),controller.signal);
      if(!valid())return;
      const images:Frame[]=[];
      try {for(const f of data.frames){const bytes=Uint8Array.from(atob(f.jpeg_base64),c=>c.charCodeAt(0));images.push({...f,url:URL.createObjectURL(new Blob([bytes],{type:"image/jpeg"}))});}}
      catch(e){images.forEach(f=>URL.revokeObjectURL(f.url));throw e;}
      published.current={data,images};setFrames(published.current);
    }catch(e){if(valid())setError(e instanceof Error?e.message:"画面提取失败。");}
    finally{if(valid()){active.current=null;setLoading(false);}}
  }
  const chosen=frames?.images.find(f=>f.index===selected);
  function selectFrame(frame:Frame){
    if(!validRender()||published.current!==frames)return;
    stopRecognition();setRecognition(null);setSelected(frame.index);
    if(destinationId&&(onConfirm||onSearchClue)&&excludedIds.length<6){
      // Own immutable bytes, not a URL whose lifetime belongs to the video preview.
      const bytes=Uint8Array.from(atob(frame.jpeg_base64),c=>c.charCodeAt(0));
      const next:Recognition={source:{file:new File([bytes],"frame.jpg",{type:"image/jpeg"}),index:frame.index,targetSeconds:frame.target_seconds},
        controller:new AbortController(),version:recognitionVersion.current,city:destinationId};
      recognitionRef.current=next;setRecognition(next);
    }
    large.current?.focus();
  }
  return <section ref={section} tabIndex={-1} aria-label="短视频画面准备" className="mb-6 space-y-4 rounded-xl border bg-stone-50 p-4">
    <div className="flex flex-wrap justify-between gap-2"><h3 className="font-semibold">短视频画面准备</h3>
      <button type="button" className={button} onClick={()=>{if(!validRender())return;invalidate();releaseFrames();releaseFile();onClose();}}>关闭视频入口</button></div>
    <p className="text-sm leading-6">选文件只在本地播放；点击“提取画面”才上传。服务端使用私有临时文件处理，完成、失败或取消后清理，不长期保存。请勿上传敏感或无权使用的内容。</p>
    <p className="text-sm leading-6">抽帧、选帧和放大不识别地点。选中一张后，明确点击“识别选中画面”才调用千问；仍须主动搜索高德、核对并确认才能加入编辑中的必去。音轨不处理，不自动改变正式行程。</p>
    <label className="block text-sm">选择一个短视频（MP4 / H.264，≤20 MiB、≤30秒、长边≤1920且≤2073600像素）
      <input type="file" accept="video/mp4,.mp4" className="mt-2 block w-full min-w-0" onChange={e=>{const next=e.target.files?.[0];e.target.value="";choose(next);}} /></label>
    {file&&<video key={file.url} src={file.url} controls muted playsInline preload="metadata" aria-label="本地视频预览" className="max-h-80 w-full rounded-lg bg-black" onError={()=>{if(validRender())setError("浏览器无法播放该文件；服务端仍会独立检查格式，未自动上传。");}}>浏览器无法播放此视频。</video>}
    <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={!file||loading} onClick={()=>void extract()}>{loading?"正在提取画面…":"提取画面"}</button>
      {loading&&<button type="button" className={button} onClick={()=>{if(!validRender())return;invalidate();setLoading(false);setError("已取消；服务端清理结束前可能仍显示忙，请稍后主动操作。");}}>取消提取</button>}</div>
    {error&&<p role="alert" className="text-sm text-red-800">{error}</p>}
    {frames&&<div className="space-y-3" aria-live="polite"><p className="text-sm">视频约{frames.data.duration_seconds.toFixed(2)}秒 · {frames.data.width}×{frames.data.height} · 取得{frames.images.length}/{frames.data.sample_count}张画面</p>
      <p className="text-sm">按等长区间中点采样，最多6张；“约”表示目标时间，非精确帧时间。可能遗漏短暂出现的地点，不保证找全景点。</p>
      {frames.images.length<frames.data.sample_count&&<p className="text-sm text-amber-800">部分目标未取得画面或画面完全重复，未复制图片凑数。</p>}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">{frames.images.map(f=><button key={f.index} type="button" className={`${button} min-w-0 p-2 ${selected===f.index?"ring-2 ring-emerald-700":""}`} aria-pressed={selected===f.index}
        aria-label={`放大画面${f.index}，约${f.target_seconds.toFixed(2)}秒`} onClick={()=>selectFrame(f)}>
        <img src={f.url} alt={`画面${f.index}缩略图`} className="h-28 w-full object-contain" />{/* eslint-disable-line @next/next/no-img-element */}
        <span className="mt-2 block">画面{f.index} · 约{f.target_seconds.toFixed(2)}秒</span></button>)}</div>
      <div ref={large} tabIndex={-1} aria-label="放大画面预览" className="rounded-lg border bg-white p-3">{chosen?<><p className="mb-2 text-sm">画面{chosen.index} · 相对视频起点约{chosen.target_seconds.toFixed(2)}秒（目标采样时间）</p>
        <img src={chosen.url} alt={`放大画面${chosen.index}`} className="max-h-[36rem] w-full object-contain" />{/* eslint-disable-line @next/next/no-img-element */}
      </>:<p className="text-sm">选择一张缩略图放大查看。</p>}</div>
    </div>}
    {chosen&&!destinationId&&<p className="text-sm">请先选择目的地后重新提取并选择画面；当前未发起识别。</p>}
    {chosen&&excludedIds.length>=6&&<p className="text-sm">必去地点已达六个，请先移除一个；当前未发起识别。</p>}
    {recognition&&destinationId===recognition.city&&<PhotoPlacePicker key={recognition.version} destinationId={recognition.city}
      sessionSignal={recognition.controller.signal} preparedFrame={recognition.source} excludedIds={excludedIds} capacityAvailable={excludedIds.length<6}
      onSearchClue={onSearchClue}
      onCancel={()=>{if(recognitionRef.current!==recognition||recognition.controller.signal.aborted)return;stopRecognition();setRecognition(null);}}
      onConfirm={place=>{if(!validRender()||recognitionRef.current!==recognition||recognition.controller.signal.aborted||destinationId!==recognition.city||excludedIds.length>=6||excludedIds.includes(place.id))return;onConfirm?.(place);}}/>}
  </section>;
}
