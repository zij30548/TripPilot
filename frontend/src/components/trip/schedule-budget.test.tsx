import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ScheduleBudget from "./schedule-budget";
import { adjustmentResponse } from "@/lib/schedule-adjustment.test-fixtures";
import { optionals, required, scheduleRequest, tripRequest } from "@/lib/weather-schedule-check.test-fixtures";
const region = () => within(screen.getByRole('region',{name:'正式草案费用与预算'}));
const summary = () => within(screen.getByRole('region',{name:'预算核对汇总'}));
const change = (name: RegExp, value: string) => fireEvent.change(region().getByRole('textbox',{name}),{target:{value}});

describe('official schedule budget panel',()=>{
  it('no valid visit/draft means no amount or sufficiency conclusion',()=>{
    const view=render(<ScheduleBudget response={null} request={tripRequest()}/>);
    expect(region().queryByRole('region',{name:'预算核对汇总'})).toBeNull();expect(region().queryByText(/¥/)).toBeNull();
    view.rerender(<ScheduleBudget response={adjustmentResponse(scheduleRequest(),'empty')} request={tripRequest()}/>);
    expect(region().getByText(/没有实际安排的游览/)).toBeTruthy();expect(region().queryByText(/¥/)).toBeNull();
  });
  it('blank, explicit zero, excess, invalid precision and correction have distinct immediate states',()=>{
    render(<ScheduleBudget response={adjustmentResponse(scheduleRequest())} request={tripRequest()}/>);
    expect(summary().getByText('尚未核算的费用（5 项）')).toBeTruthy();
    expect(summary().getByText(/不是可自由支配余额/)).toBeTruthy();
    change(/全程其他费用/,'0'); expect(summary().getByText('尚未核算的费用（4 项）')).toBeTruthy();expect(region().getByText(/¥0.00 · 用户估算，未核实免费/)).toBeTruthy();
    change(/全程住宿总额/,'3001');expect(summary().getByText('已计入部分已超过预算 ¥1.00。')).toBeTruthy();
    change(/全程住宿总额/,'3.001');expect(summary().getByRole('alert').textContent).toContain('暂停显示小计');expect(summary().queryByText(/当前可计算小计：/)).toBeNull();
    expect(region().getByRole('textbox',{name:/全程住宿总额/}).getAttribute('aria-invalid')).toBe('true');
    for (const textbox of region().getAllByRole('textbox')) fireEvent.change(textbox,{target:{value:'0'}});
    expect(summary().getByText(/当前范围内费用字段已填齐/)).toBeTruthy();expect(summary().queryByText(/预算充足/)).toBeNull();
    expect(summary().getByText('当前可计算小计：¥0.00')).toBeTruthy();
  });
  it('unknown transit has no override and scheme fare/person totals are explicit with provenance',()=>{
    const response=adjustmentResponse({...scheduleRequest(),transport_mode:'transit'});response.edges[0].transit_route!.fare_cny=2.5;
    render(<ScheduleBudget response={response} request={{...tripRequest(),travelers:3}}/>);
    const transport=within(region().getByRole('region',{name:'交通费用明细'}));
    expect(transport.getByText(/每人参考票价 ¥2.50 × 3 人 · 本次计入 ¥7.50/)).toBeTruthy();
    expect(transport.getAllByText(/每人参考票价 未知 × 3 人/)).toHaveLength(2);
    expect(transport.queryByRole('textbox')).toBeNull(); expect(transport.getAllByText(/来源：高德公交参考方案票价/)).toHaveLength(3);
    expect(summary().getByText(/交通参考已知小计：¥7.50/)).toBeTruthy();expect(summary().getByText('尚未核算的费用（7 项）')).toBeTruthy();
  });
  it('preserves same-ID user text through invalidation/adoption, removed IDs do not count; new IDs unknown; unmount clears',()=>{
    const original=adjustmentResponse(scheduleRequest()), request=tripRequest();
    const view=render(<ScheduleBudget response={original} request={request}/>);
    const tickets=region().getAllByRole('textbox',{name:/门票／入场费/});
    fireEvent.change(tickets[0],{target:{value:'10'}});fireEvent.change(tickets[1],{target:{value:'20'}});
    change(/全天餐饮费/,'30');change(/全程住宿总额/,'100');change(/全程其他费用/,'5');
    expect(summary().getByText('当前可计算小计：¥165.00')).toBeTruthy();
    const next=adjustmentResponse({...scheduleRequest(),optional_places:[optionals[1]],duration_settings:[required,optionals[1]].map(p=>({place_id:p.id,minutes:60,source:'default'}))});
    view.rerender(<ScheduleBudget response={next} request={request}/>);
    expect(summary().getByText('当前可计算小计：¥145.00')).toBeTruthy();
    expect((region().getByRole('textbox',{name:/测试可选2.*门票/}) as HTMLInputElement).value).toBe('');
    view.rerender(<ScheduleBudget response={null} request={request}/>);expect(region().queryByRole('region',{name:'预算核对汇总'})).toBeNull();
    view.rerender(<ScheduleBudget response={original} request={request}/>);expect(summary().getByText('当前可计算小计：¥165.00')).toBeTruthy();
    view.unmount();render(<ScheduleBudget response={original} request={request}/>);expect(region().getAllByRole('textbox').every(i=>(i as HTMLInputElement).value==='')).toBe(true);
  });
});
