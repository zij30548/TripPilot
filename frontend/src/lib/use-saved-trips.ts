"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { createSnapshot, listTrips, readTrip, saveTrip, validSaveName, type SaveOperation, type SavedDetail, type SavedList } from "./saved-trips";
import type { TripRequest } from "@/types/trip";
import type { CostInputs } from "./schedule-costs";
import type { useSchedulePreview } from "./use-schedule-preview";

type SaveState = { status:"idle"|"saving"|"uncertain"|"saved"|"invalid"; operation:SaveOperation|null; detail:SavedDetail|null; message:string };
export function useSaveTrip(schedule:ReturnType<typeof useSchedulePreview>, requirements:TripRequest, getCosts:()=>CostInputs, canOperate?:()=>boolean, canRetryFrozen?:()=>boolean) {
  const [state,setState]=useState<SaveState>({status:"idle",operation:null,detail:null,message:""});
  const ref=useRef(state), serial=useRef(0), controller=useRef<AbortController|null>(null);
  const alive=useRef(true);
  const publish=(s:SaveState)=>{ref.current=s;setState(s);};
  const run=async (operation:SaveOperation, retryFrozen=false) => {
    const authorized=retryFrozen ? (canRetryFrozen ?? canOperate) : canOperate;
    if(!alive.current||authorized?.()===false||ref.current.status==="saving") return;
    controller.current?.abort(); const abort=new AbortController();controller.current=abort;const version=++serial.current;
    publish({status:"saving",operation,detail:null,message:"正在保存点击时捕获的快照…"});
    try {
      const detail=await saveTrip(operation,abort.signal);
      if(serial.current!==version||abort.signal.aborted)return;
      publish({status:"saved",operation,detail,message:`已保存“${operation.name}”的点击时快照；之后的修改不会自动保存。`});
    }catch(error){
      if(serial.current!==version||abort.signal.aborted)return;
      publish({status:"uncertain",operation,detail:null,message:error instanceof Error?error.message:"保存结果未确认，可重试冻结快照。"});
    }
  };
  const save=(name:string)=>{
    if(!alive.current||canOperate?.()===false||ref.current.status==="saving")return;
    try{
      const capture=schedule.capture();
      if(!capture||!validSaveName(name))throw new Error("请先生成有效正式草案，并填写 1～80 字的存档名称。");
      const operation={operation_id:crypto.randomUUID().replaceAll("-",""),name,snapshot:createSnapshot(requirements,capture.original,getCosts())};
      return run(operation);
    }catch(error){publish({status:"invalid",operation:null,detail:null,message:error instanceof Error?error.message:"快照无效。"});}
  };
  const retry=()=>ref.current.operation&&ref.current.status==="uncertain"?run(ref.current.operation,true):undefined;
  const cancel=()=>{
    if(ref.current!==state||state.status!=="saving")return;
    ++serial.current;controller.current?.abort();
    publish({...state,status:"uncertain",message:"已停止等待，但数据库可能已提交。重试会沿用这份快照和标识，不会自动重复建档。"});
  };
  const dispose=useCallback(()=>{alive.current=false;++serial.current;controller.current?.abort();},[]);
  const cancelPending=()=>{
    const active=ref.current;
    if(active.status!=="saving")return;
    ++serial.current;controller.current?.abort();
    publish({...active,status:"uncertain",message:"已停止等待，但数据库可能已提交。重试会沿用这份快照和标识。"});
  };
  useEffect(()=>{alive.current=true;return dispose;},[dispose]);
  return {state,readState:()=>ref.current,save,retry,cancel,cancelPending};
}

type HistoryState={status:"idle"|"loading"|"list"|"detail"|"error";list:SavedList|null;detail:SavedDetail|null;error:string;offset:number};
export function useSavedHistory(){
  const [state,setState]=useState<HistoryState>({status:"idle",list:null,detail:null,error:"",offset:0});
  const current=useRef(state);
  const publish=useCallback((next:HistoryState)=>{current.current=next;setState(next);},[]);
  const version=useRef(0),controller=useRef<AbortController|null>(null);
  const load=useCallback(async(id:string|null,offset=0)=>{
    controller.current?.abort();const abort=new AbortController();controller.current=abort;const generation=++version.current;
    publish({status:"loading",list:null,detail:null,error:"",offset});
    try{
      const data=id?await readTrip(id,abort.signal):await listTrips(offset,abort.signal);
      if(generation!==version.current||abort.signal.aborted)return;
      publish({status:id?"detail":"list",list:id?null:data as SavedList,detail:id?data as SavedDetail:null,error:"",offset});
    }catch(error){if(generation===version.current&&!abort.signal.aborted)publish({status:"error",list:null,detail:null,offset,error:error instanceof Error?error.message:"历史读取失败。"});}
  },[publish]);
  useEffect(()=>()=>{++version.current;controller.current?.abort();},[]);
  const cancel=()=>{++version.current;controller.current?.abort();publish({status:"idle",list:null,detail:null,error:"",offset:0});};
  return{state,readState:()=>current.current,load,cancel};
}
