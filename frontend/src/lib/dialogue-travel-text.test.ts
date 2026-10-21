import { describe, expect, it } from "vitest";
import { adjustmentDialogueText, costsDialogueText, historyDialogueText, scheduleDialogueText, weatherDialogueText } from "./dialogue-travel-text";
import { calculateScheduleCosts, emptyCostInputs } from "./schedule-costs";
import { adjustmentResponse, captureFixture } from "./schedule-adjustment.test-fixtures";
import { schedule, scheduleRequest, tripRequest, forecast } from "./weather-schedule-check.test-fixtures";
import { savedFixture } from "./restore-trip.test-fixtures";

describe("plain dialogue facts stay deterministic and attributed",()=>{
  it("a complete timeline includes actual moves, stays, return and source times, not fabricated restaurants",()=>{
    const text=scheduleDialogueText(schedule());
    expect(text).toContain("步行");expect(text).toContain("停留 60 分钟，默认设置");expect(text).toContain("预计返回住宿参考点");
    expect(text).toContain("查询于 2026-10-10T01:00:00Z");expect(text).toContain("不保证旅行当天");
  });
  it("failure explains the first required blocker and does not turn empty days into visits or midnight",()=>{
    const result=adjustmentResponse(scheduleRequest(),"empty"),text=scheduleDialogueText(result);
    expect(text).toContain("首先未能安排");expect(text).toContain("0/1");expect(text).toContain("当天未安排游览");
    expect(text).toContain("必去未排齐，因此未尝试");expect(text).not.toContain("住宿参考点：00:00");
  });
  it("unknown transit fares and blank user costs remain visible and cannot become free balance",()=>{
    const result=adjustmentResponse({...scheduleRequest(),transport_mode:"transit"});
    const text=costsDialogueText(calculateScheduleCosts(result,tripRequest(),emptyCostInputs()));
    expect(text).toContain("参考票价未知");expect(text).toContain("不能作为可自由支配余额");
    expect(text).toContain("每位旅客");expect(text).toContain("用户估算");expect(text).toContain("尚未核算");
    expect(text).toContain("当前正式草案费用");
  });
  it("an invalid amount is reported without retaining an old complete total",()=>{
    const inputs={...emptyCostInputs(),other:"NaN"};
    const text=costsDialogueText(calculateScheduleCosts(schedule(),tripRequest(),inputs));
    expect(text).toContain("存在非法金额");expect(text).toContain("当前可计算小计：无法安全核算");expect(text).not.toContain("预算减当前小计：");
  });
  it("preview text does not call lower known subtotal saving when either side has unknown costs",()=>{
    const capture=captureFixture(),next=adjustmentResponse({...capture.original.request,optional_places:[],duration_settings:capture.original.request.duration_settings.slice(0,1)});
    const text=adjustmentDialogueText(capture.original,next,"同名地点",tripRequest(),emptyCostInputs());
    expect(text).toContain("完整费用增减暂不可计算");expect(text).toContain("不同查询批次");expect(text).toContain("此次没有补入新地点");expect(text).not.toContain("新方案减少");
    expect(text).toContain("原正式方案费用");expect(text).toContain("调整预览费用（尚未采用）");expect(text).not.toContain("当前正式草案费用");
  });
  it("history preserves original saved and generated time and does not relabel it current",()=>{
    const value=savedFixture(),text=historyDialogueText(value);
    expect(text).toContain(value.saved_at);expect(text).toContain(value.snapshot.schedule.generated_at);expect(text).toContain("不会覆盖当前正式草案");
    expect(text).toContain("历史快照费用（未重新核验）");expect(text).not.toContain("当前正式草案费用");
  });
  it("history uses the same page-local one-based numbering as exact selection",()=>{
    const first=savedFixture(),second={...savedFixture({},"b".repeat(32)),name:"另一份历史行程"};
    const text=historyDialogueText({items:[first,second],offset:20,limit:20,has_more:false});
    expect(text).toContain(`1. ${first.name}`);expect(text).toContain(`2. ${second.name}`);expect(text).not.toContain(`21. ${first.name}`);
  });
  it("history gives a bounded next-page command and does not label an empty later page an empty account",()=>{
    const first=savedFixture();
    expect(historyDialogueText({items:[first],offset:0,limit:10,has_more:true})).toContain("查看第2页历史");
    const end=historyDialogueText({items:[first],offset:10000,limit:10,has_more:true});
    expect(end).toContain("达到本聊天入口分页范围");expect(end).not.toContain("查看第1002页历史");
    const empty=historyDialogueText({items:[],offset:10,limit:10,has_more:false});
    expect(empty).toContain("第2页没有记录");expect(empty).not.toContain("当前账号没有已保存");
  });
  it("weather missing date coverage stays missing, with source publish and query time",()=>{
    const value=forecast();value.days[0]={date:value.days[0].date,status:"unavailable",day:null,night:null};
    const text=weatherDialogueText(value);expect(text).toContain("无覆盖");expect(text).toContain(value.queried_at);expect(text).toContain(value.reported_at!);expect(text).not.toContain("晴");
  });
});
