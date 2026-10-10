import type { ComponentProps } from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TripPlanResult from '@/components/trip-plan-result';
import type PlaceExplorer from '@/components/places/place-explorer';
import { candidateResponse, deferred, forecast, now, optionals, plan, required, stamp } from '@/lib/weather-schedule-check.test-fixtures';
import { adjustmentResponse } from '@/lib/schedule-adjustment.test-fixtures';
import { ScheduleError } from '@/lib/schedule-api';
import type { ScheduleRequest, ScheduleResponse } from '@/types/schedule';

const api=vi.hoisted(()=>({weather:vi.fn(),candidates:vi.fn(),schedule:vi.fn(),walking:vi.fn()}));
vi.mock('@/lib/weather-api',async original=>({...await original<typeof import('@/lib/weather-api')>(),queryWeatherForecast:api.weather}));
vi.mock('@/lib/candidates-api',async original=>({...await original<typeof import('@/lib/candidates-api')>(),queryCandidates:api.candidates}));
vi.mock('@/lib/schedule-api',async original=>({...await original<typeof import('@/lib/schedule-api')>(),querySchedulePreview:api.schedule}));
vi.mock('@/lib/routes-api',async original=>({...await original<typeof import('@/lib/routes-api')>(),queryWalkingRoute:api.walking}));
vi.mock('@/components/places/place-explorer',()=>({default:(props:ComponentProps<typeof PlaceExplorer>)=><section aria-label="测试地图">
  {props.bindingTarget&&<button onClick={()=>props.bindingTarget!.onConfirm(props.bindingTarget!.keyword==='测试活动甲'?required:optionals[0])}>测试确认地点</button>}
  {props.itinerary?.places.map(p=><button key={p.id} onClick={()=>props.itinerary!.onSelect(p.id)}>测试 Marker {p.id}</button>)}
  <output data-testid="cost-map-state">{JSON.stringify({ids:props.itinerary?.places.map(p=>p.id),selected:props.itinerary?.selectedPlaceId,walking:props.walkingRoute,transit:props.transitRoute})}</output>
</section>}));
const click=async(name:string)=>{await act(async()=>fireEvent.click(screen.getByRole('button',{name})));};
const budget=()=>within(screen.getByRole('region',{name:'正式草案费用与预算'}));
const fill=()=>{budget().getAllByRole('textbox').forEach((input,i)=>fireEvent.change(input,{target:{value:String((i+1)*10)}}));};
const total=()=>budget().getByRole('region',{name:'预算核对汇总'}).textContent;
const counts=()=>[api.schedule.mock.calls.length,api.weather.mock.calls.length,api.candidates.mock.calls.length,api.walking.mock.calls.length];
async function ready(){const input=plan();input.estimated_cost=999999;input.budget_breakdown={food:22222,transport:33333,tickets:44444,other:55555};const view=render(<TripPlanResult plan={input} onEdit={vi.fn()}/>);await click('获取候选地点');await click('生成行程草案');await click('查询天气');await click('检查天气对行程的影响');return {view,input};}
beforeEach(()=>{vi.useFakeTimers({toFake:['Date']});vi.setSystemTime(now);vi.clearAllMocks();api.schedule.mockImplementation(async(r:ScheduleRequest)=>adjustmentResponse(r));api.candidates.mockImplementation(async()=>candidateResponse());api.weather.mockImplementation(async()=>forecast());api.walking.mockResolvedValue({status:'ok',source:'amap',queried_at:stamp,route:{distance_meters:80,duration_seconds:60,segments:[[[121.01,31],[121.1,31]]]}});});
afterEach(()=>vi.useRealTimers());
describe('budget independence in the actual result composition',()=>{
  it('preview loading/completion/cancel/failure keep formal costs; adopt updates scope only, preserving other totals',async()=>{
    await ready();fill();expect(total()).toContain('当前可计算小计：¥150.00');expect(total()).not.toContain('999,999');
    const before=total(),pending=deferred<ScheduleResponse>();api.schedule.mockReturnValueOnce(pending.promise);
    await click('预览排除该地点后的草案');expect(total()).toBe(before);
    // Cost edit during a pending preview neither aborts it nor invalidates authority.
    const n=counts();fireEvent.change(budget().getByRole('textbox',{name:/全程其他费用/}),{target:{value:'60'}});expect(counts()).toEqual(n);expect(api.schedule.mock.calls[1][1].aborted).toBe(false);
    await act(async()=>pending.resolve(adjustmentResponse(api.schedule.mock.calls[1][0])));
    expect(screen.getByRole('button',{name:'采用此方案'}).hasAttribute('disabled')).toBe(false);
    const updated=total();await click('取消预览');expect(total()).toBe(updated);expect(counts()).toEqual(n);
    api.schedule.mockRejectedValueOnce(new ScheduleError('timeout','受控超时'));await click('预览排除该地点后的草案');expect(total()).toBe(updated);
    await click('取消预览');await click('预览排除该地点后的草案');const beforeAdopt=counts();await click('采用此方案');expect(counts()).toEqual(beforeAdopt);
    expect(total()).toContain('当前可计算小计：¥140.00'); // retained 10 + meals30 + lodging40 + other60; removed20, new unknown
    expect(total()).toContain('测试可选2 · 门票／入场费：未知');
    expect(screen.getByRole('button',{name:'恢复可选地点：同名地点'})).toBeTruthy();
    expect((budget().getByRole('textbox',{name:/全程住宿总额/}) as HTMLInputElement).value).toBe('40');
    await click('生成行程草案');expect(total()).toContain('当前可计算小计：¥140.00');
  });
  it('fees do not invalidate check/preview or old map; weather/annotations/map do not change budget',async()=>{
    await ready();fill();await click('预览排除该地点后的草案');
    for(const label of ['甲','乙']){await click(`为测试活动${label}绑定地点`);await click('测试确认地点');}
    await click('查询步行路线');await click(`测试 Marker ${required.id}`);
    const previous=counts(),map=screen.getByTestId('cost-map-state').textContent,preview=screen.getByRole('region',{name:'新旧地点对比'}).textContent;
    fireEvent.change(budget().getByRole('textbox',{name:/全程其他费用/}),{target:{value:'0'}});
    expect(counts()).toEqual(previous);expect(screen.getByTestId('cost-map-state').textContent).toBe(map);expect(screen.getByRole('region',{name:'新旧地点对比'}).textContent).toBe(preview);
    expect(screen.getByRole('button',{name:'采用此方案'}).hasAttribute('disabled')).toBe(false);
    const value=total();await click('刷新天气');expect(total()).toBe(value);await click('检查天气对行程的影响');
    fireEvent.change(within(screen.getByRole('region',{name:'天气与行程检查'})).getAllByRole('combobox')[0],{target:{value:'indoor'}});expect(total()).toBe(value);
    await click(`测试 Marker ${optionals[0].id}`);expect(total()).toBe(value);
  });
  it.each(['candidate','stay','mode','query'])('%s invalidation hides old totals, ordinary regeneration retains user estimates',async(kind)=>{
    await ready();fill();
    if(kind==='candidate')await click('排除可选地点：同名地点');
    if(kind==='stay')fireEvent.change(screen.getByRole('spinbutton',{name:'停留分钟：同名地点'}),{target:{value:'90'}});
    if(kind==='mode')fireEvent.click(within(screen.getByRole('region',{name:'行程草案'})).getByRole('button',{name:'公交／地铁'}));
    if(kind==='query'){const pending=deferred<ScheduleResponse>();api.schedule.mockReturnValueOnce(pending.promise);await click('生成行程草案');expect(budget().queryByRole('region',{name:'预算核对汇总'})).toBeNull();await act(async()=>pending.reject(new Error('controlled generation failure')));}
    expect(budget().queryByRole('region',{name:'预算核对汇总'})).toBeNull();
    await click('生成行程草案');expect((budget().getByRole('textbox',{name:/全程住宿总额/}) as HTMLInputElement).value).toBe('40');
  });
  it('leaving result and remounting clears session estimates',async()=>{
    const {view,input}=await ready();fill();fireEvent.click(screen.getAllByRole('button',{name:'修改旅行需求'})[0]);view.unmount();render(<TripPlanResult plan={input} onEdit={vi.fn()}/>);await click('获取候选地点');await click('生成行程草案');expect(budget().getAllByRole('textbox').every(i=>(i as HTMLInputElement).value==='')).toBe(true);
  });
});

const comparison=()=>within(screen.getByRole('region',{name:'调整预览费用对比'}));
const side=(label:'原方案费用'|'预览方案费用')=>within(comparison().getByRole('region',{name:label}));
const fillNew=(value:string)=>fireEvent.change(within(comparison().getByRole('region',{name:'预览新增地点费用'})).getByRole('textbox'),{target:{value}});
describe('shared costs in real adjustment composition',()=>{
  it('new-only fee changes preview, common fees update both; cancel/reopen retains input and adopt equals the viewed subtotal',async()=>{
    await ready();fill();await click('预览排除该地点后的草案');const n=counts(),original=total();
    expect(comparison().getByText('完整费用增减：暂不可计算')).toBeTruthy();expect(side('预览方案费用').getByText('当前可计算小计：¥130.00')).toBeTruthy();
    fillNew('10');expect(total()).toBe(original);expect(comparison().getByText('按当前参考票价和用户估算，新方案减少 ¥10.00')).toBeTruthy();
    fireEvent.change(budget().getByRole('textbox',{name:/全程其他费用/}),{target:{value:'60'}});
    expect(side('原方案费用').getByText('当前可计算小计：¥160.00')).toBeTruthy();expect(side('预览方案费用').getByText('当前可计算小计：¥150.00')).toBeTruthy();expect(counts()).toEqual(n);
    await click('取消预览');expect(total()).toContain('当前可计算小计：¥160.00');expect(counts()).toEqual(n);expect(screen.getByText(/你主动填写的费用估算仍在本次结果中保留/)).toBeTruthy();
    await click('预览排除该地点后的草案');expect((comparison().getByRole('textbox') as HTMLInputElement).value).toBe('10');
    const expected=side('预览方案费用').getByText('当前可计算小计：¥150.00').textContent, beforeAdopt=counts();await click('采用此方案');
    expect(counts()).toEqual(beforeAdopt);expect(total()).toContain(expected);expect(screen.queryByRole('region',{name:'调整预览费用对比'})).toBeNull();
    expect((budget().getByRole('textbox',{name:/测试可选2.*门票/}) as HTMLInputElement).value).toBe('10');
    await click('生成行程草案');expect(total()).toContain(expected);
  });
  it.each(['','bad','9000'])('unknown/invalid/excess %s changes no route adoption rule',async(value)=>{
    await ready();fill();await click('预览排除该地点后的草案');fillNew(value);const n=counts();
    expect(screen.getByRole('button',{name:'采用此方案'}).hasAttribute('disabled')).toBe(false);
    if(value!=='9000')expect(comparison().getByText('完整费用增减：暂不可计算')).toBeTruthy();
    if(value==='bad')expect(side('预览方案费用').getByRole('alert').textContent).toContain('金额无效');
    if(value==='9000')expect(comparison().getByText('按当前参考票价和用户估算，新方案增加 ¥8,980.00')).toBeTruthy();
    await click('采用此方案');expect(counts()).toEqual(n);expect((budget().getByRole('textbox',{name:/测试可选2.*门票/}) as HTMLInputElement).value).toBe(value);
  });
  it('previously displayed delta vanishes on invalid common input, with errors on both sides; correcting to zero restores it',async()=>{
    await ready();fill();await click('预览排除该地点后的草案');fillNew('20');expect(comparison().getByText('按当前参考票价和用户估算，估算金额相同')).toBeTruthy();
    const n=counts();fireEvent.change(budget().getByRole('textbox',{name:/全程住宿总额/}),{target:{value:'1.001'}});
    expect(comparison().queryByText('按当前参考票价和用户估算，估算金额相同')).toBeNull();expect(comparison().getAllByRole('alert')).toHaveLength(2);expect(comparison().getByText('完整费用增减：暂不可计算')).toBeTruthy();
    fireEvent.change(budget().getByRole('textbox',{name:/全程住宿总额/}),{target:{value:'0'}});expect(comparison().getByText('按当前参考票价和用户估算，估算金额相同')).toBeTruthy();expect(counts()).toEqual(n);
  });
  it.each(['weather','query','candidate','stay','mode','annotation','midnight'])('%s expires comparison and new-place editor, retains inspectable old timeline',async(kind)=>{
    await ready();fill();await click('预览排除该地点后的草案');fillNew('10');
    if(kind==='weather')await click('刷新天气');
    if(kind==='query')await click('生成行程草案');
    if(kind==='candidate')await click('排除可选地点：测试可选3');
    if(kind==='stay')fireEvent.change(screen.getByRole('spinbutton',{name:'停留分钟：同名地点'}),{target:{value:'90'}});
    if(kind==='mode')fireEvent.click(within(screen.getByRole('region',{name:'行程草案'})).getByRole('button',{name:'公交／地铁'}));
    if(kind==='annotation')fireEvent.change(within(screen.getByRole('region',{name:'天气与行程检查'})).getAllByRole('combobox')[0],{target:{value:'indoor'}});
    if(kind==='midnight'){vi.setSystemTime('2026-10-10T16:00:00Z');act(()=>window.dispatchEvent(new Event('focus')));}
    expect(screen.queryByRole('region',{name:'调整预览费用对比'})).toBeNull();expect(screen.queryByRole('region',{name:'预览新增地点费用'})).toBeNull();
    expect(screen.getByText('查看原方案完整时间轴与安排情况')).toBeTruthy();expect(screen.getByRole('button',{name:'采用此方案'}).hasAttribute('disabled')).toBe(true);
  });
  it('pending/failed/invalid or empty required-incomplete preview never exposes comparison or editor',async()=>{
    await ready();fill();const pending=deferred<ScheduleResponse>();api.schedule.mockReturnValueOnce(pending.promise);await click('预览排除该地点后的草案');
    expect(screen.queryByRole('region',{name:'调整预览费用对比'})).toBeNull();await act(async()=>pending.reject(new ScheduleError('timeout','受控超时')));
    expect(screen.queryByRole('region',{name:'调整预览费用对比'})).toBeNull();await click('取消预览');
    api.schedule.mockResolvedValueOnce({status:'complete'});await click('预览排除该地点后的草案');expect(screen.queryByRole('region',{name:'调整预览费用对比'})).toBeNull();await click('取消预览');
    api.schedule.mockImplementationOnce(async(r)=>adjustmentResponse(r,'empty'));await click('预览排除该地点后的草案');
    expect(screen.getByText('查看调整预览完整时间轴与未安排原因')).toBeTruthy();expect(screen.queryByRole('region',{name:'调整预览费用对比'})).toBeNull();
  });
  it('a valid required-only partial compares costs without a new ticket editor',async()=>{
    await ready();fill();api.schedule.mockImplementationOnce(async(r)=>adjustmentResponse(r,'required_only'));await click('预览排除该地点后的草案');
    expect(comparison().getByText('按当前参考票价和用户估算，新方案减少 ¥20.00')).toBeTruthy();expect(comparison().queryByRole('textbox')).toBeNull();expect(screen.getByRole('button',{name:'采用此方案'}).hasAttribute('disabled')).toBe(false);
  });
});
