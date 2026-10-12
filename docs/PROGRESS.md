# TripPilot Progress

## Completed

- Project repository created
- Next.js frontend initialized
- FastAPI backend initialized
- Frontend runs locally
- Backend health endpoint runs locally
- Responsive Shanghai 1-3 day trip request form
- Pydantic TripRequest validation and typed TripPlan / DayPlan / Activity response models
- POST /trips/plan returning a fixed Shanghai two-day Mock plan
- Local CORS configuration for localhost:3000 and 127.0.0.1:3000
- Frontend fetch integration with loading, error, success and duplicate-submit protection
- Componentized Trip Result: overview, daily timeline, activity cards, transport segments, budget breakdown, Mock weather and map placeholder
- Form → loading → full-width result flow, with day switching and an edit button that preserves form input
- Expanded Pydantic and TypeScript response models, with runtime response validation
- Backend validation and API tests
- Independent GET /places/search with AMap Web Service POI 2.0 keyword search and typed Place responses
- Backend-only AMAP_WEB_KEY configuration from environment or backend/.env; safe errors and request-log key redaction
- Mock-only tests for POI conversion, upstream failures, query validation and configuration
- Milestone 4A implementation: browser-only AMap JS API 2.0 loader, Shanghai Place search, candidate cards, markers, bidirectional selection and fit-all controls
- Restricted backend serviceHost proxy with server-only JS security code, fixed upstream, credential checks, safe responses and HTTP log redaction
- Frontend offline component/API/SDK tests and backend proxy security regression tests
- Milestone 4B: explicitly confirmed activity-to-real-POI bindings, replacement/unbinding, per-day bound maps, activity ↔ marker selection, POI deduplication and result-local lifecycle protection
- Milestone 4C-1: explicitly requested real walking routes between originally adjacent, confirmed bound activities; normalized backend route data, estimated distance/time, one selected map polyline and stale-request isolation
- Milestone 4C-2: explicitly requested Shanghai bus/subway reference schemes, normalized ordered access-walking/ride steps, nullable fare, segmented real map geometry and cross-mode stale-request isolation
- Milestone 5A-1: explicitly confirmed accommodation reference and optional must-visit POIs, strict backward-compatible request/echo validation, isolated search drafts and confirmed requirements displayed separately from the Mock plan
- Milestone 5A-2: explicitly requested Shanghai interest-based candidate preparation, bounded backend aggregation, must-visit snapshot preservation, deterministic optional selection and result-local exclusion/restoration
- Milestone 5B-1: independent fixed-order must-visit walking previews, actual 1–3-day dates, editable stays/lunch, bounded real walking queries, verified daily returns and explicit unscheduled reasons
- Milestone 5B-2: current non-excluded optional candidates participate in the same walking preview, with required-first scheduling and at most one fixed-tail addition per day, three optional inputs and one shared route-query budget
- Milestone 5B-3: one explicitly selected walking or Shanghai transit-reference mode per schedule preview, mode-aware route acquisition/validation, actual bus/subway steps and synchronous cross-mode stale-request isolation
- Milestone 5C-1: explicitly queried Shanghai daily forecasts, independent date-matched snapshots, day/night rain/snow text rules, source-publication freshness and isolated result-page lifecycle
- Milestone 5C-2: user-labeled visit exposure, explicit local weather/schedule association, version-bound explainable checks, and explicit optional exclusion through the existing candidate → draft invalidation flow
- Milestone 5C-3: isolated optional-exclusion adjustment previews, POI/date-based before/after comparison, cancellation preserving the official draft, and explicitly confirmed synchronous adoption
- Milestone 5D-1: local cost/budget checking for the official schedule, occurrence-based reference fares, ID/date-scoped user estimates, exact integer-cent arithmetic and explicit unknown-cost boundaries
- Milestone 5D-2: adjustment-preview cost comparison using shared current estimates, preview-only new-place inputs, signed exact differences only for complete known costs, and unchanged itinerary adoption eligibility

## Current

- Milestone **6C-1 unified formal planning entry is implemented and accepted**, based on verified pushed `main` **`1a25cff0b6809699bcd8572560eeddab00974784`** (6B-2). This **26-file frontend/document delivery** moves the default homepage directly from confirmed TripRequest to the shared editable PlanningWorkspace; `/demo` preserves the explicit legacy Mock/map integration example. Final frontend **1,020 tests / 49 files**, lint, typecheck, Webpack production build and diff checks pass. Actual production Chrome passed new → real required-only/optional walking drafts → costs → preview/cancel/adopt → save → new-tab history copy → regenerate/another save, plus 400px/keyboard and independent real map/Marker regression. Twenty individually opened screenshots and sanitized counts are in stable `6c-1` evidence below. **0 Qwen calls**, cumulative **20/20** unchanged. Ordinary stage-only commit/push follows final manifest/security/remote checks; five original cache contents remain untouched and excluded. Earlier Current/Next entries remain historical records.
- Milestone **6B-2 historical snapshots → independent editable copies is implemented and accepted** on verified pushed `main` baseline **`45a14edcee1463188925a0dbe99f20853186eedd`** (6B-1). This **23-file frontend/document delivery** keeps `saved-trip-v1`, the database and backend unchanged. History opens a fresh tab with input-only restoration; only an explicit newly validated generation becomes saveable. This round: frontend **1,011 tests / 48 files**, lint, typecheck, Webpack build and whitespace checks passed; **9 targeted backend archive tests** passed on temporary databases (not a full backend rerun). Actual production Chrome restored an existing archive with no business queries, regenerated real walking routes with changed stay/lunch, cleared estimates on confirmed traveler change, saved a distinct record during an unadopted adjustment, re-read it after reload, and verified original full-snapshot hashes unchanged. Sixteen individually opened screenshots and a sanitized receipt are in the stable `6b-2` directory below. **0 Qwen calls**, cumulative **20/20** unchanged. Normal stage-only commit/push follows final manifest/security/remote checks; the five original cache contents remain untouched and excluded. Prior Current/Next statements below describe their historical rounds.
- Milestone **6B-1 SQLite snapshot saving and read-only history are implemented and accepted** on verified pushed `main` baseline **`582a9100e143e2a2a963122d14a429c9ada39faf`** (6A-2). This **20-file delivery** adds explicit frozen saves, idempotent database transactions, bounded history pagination and strictly validated read-only replay. This round: backend **318 tests**, frontend **980 tests / 44 files**, lint, typecheck and Webpack build passed; final read-only-copy follow-up passed its **5 targeted tests** plus lint/typecheck/build. Real Chrome saved two distinct actual walking drafts, verified original-vs-adopted snapshots, immutable fees, reload and backend-restart persistence, 400px history and real map/Marker regression. Thirteen opened screenshots and a sanitized receipt are in stable `6b-1` evidence below. **0 new Qwen calls**; cumulative **20/20** remains exhausted. Normal stage-only commit/push follows final manifest/security/remote checks; five original Python caches remain untouched and excluded. Older current entries describe their historical rounds, not today's HEAD or remote status
- Milestone **6A-2 implementation and its budgeted real acceptance are complete** on verified pushed `main` baseline **`c23ad4055131756248ca5092b2d4c52f7d84d280`** (6A-1). This **17-file delivery** adds only natural-language preview of excluding one currently scheduled optional POI. Backend **309 tests**, frontend **963 tests / 41 files**, lint, typecheck and Webpack production build pass this round. Two real Qwen requests passed business validation (execute and clarify), exhausting the inherited allowance at **20 / 20 cumulative**, not a reset allowance. Nine individually opened screenshots and a sanitized receipt are in the stable `6a-2` directory below. Cancellation/AI-retention, adverse responses and races are controlled-test coverage this round, not additional live-Qwen claims. Ordinary stage-only commit/push follows final security/manifest/remote checks; the five original Python caches remain untouched and excluded. Older closeout entries below retain their historical status, not current remote state
- Milestone **6A-1 implementation, offline checks and real Qwen/Chrome acceptance are complete**, on verified `main` baseline `796280180ffd6b104d3ff5840ee68d1cb8f71028` (5D-2). This **22-file delivery** adds single-provider, explicit-click explanation of the official schedule only. Final backend **300 tests**, frontend **931 tests / 38 files**, lint, typecheck and Webpack build pass. **18 / 20 real model calls** were made; no more calls are planned. Twelve controlled business samples and three actual-browser explanations passed business checks; an initial harness parsing error and two safely rejected reference failures remain recorded, not hidden as successes. Ten individually opened screenshots and a sanitized receipt are in the stable external directory below. Normal commit/push is authorized after final manifest, sensitive-information, whitespace and remote checks; five original Python cache hashes remain unchanged and excluded
- Milestone 5D-2 implementation and acceptance are complete on verified, pushed baseline `04cac8500925cc62a87b1d02c8cfd14563bf6e64` (5D-1), branch `main`. This **12-file delivery** changes frontend code/tests and this document only. Frontend **904 tests**, lint, typecheck, Webpack build and whitespace checks pass. Eleven individually opened screenshots and a minimal sanitized receipt are in the stable external `5d-2` directory below. Normal commit/push is authorized after final manifest/security/remote checks; all five original Python caches remain untouched and excluded. Real bus comparison used a valid required-only partial: all three optional trials returned `data_error`, so this is not a claim of successful bus-mode optional replacement. Historical records below are retained, not reclassified as current executions
- Milestone 5D-1 implementation and acceptance are complete on verified, previously pushed baseline `5a11561b21076f19e960e6511855ed9ec59bf830` (5C-3), branch `main`. This **7-file delivery** changes frontend code/tests and this document only. Frontend **874 tests**, lint, typecheck, Webpack build and whitespace checks pass. Fourteen individually opened screenshots and a minimal sanitized receipt are in the stable external `5d-1` directory below. Ordinary commit/push is authorized after final manifest/security/remote checks; no force push, reset or amend. All five original Python caches remain untouched and excluded. Older current/closeout entries below are historical records, not this round's execution or present push status
- Milestone 5C-3 implementation and acceptance are complete on verified baseline `ec558900d2a037d334910589edf0c7596b2d29f6`. This **18-file delivery** changes only frontend code/tests and this document. Frontend **815 tests**, lint, typecheck, Webpack build and whitespace checks pass. Fifteen individually opened screenshots and a sanitized receipt are in the stable external `5c-3` directory below. Normal commit/push is authorized; refreshed `origin/main` equals the baseline (zero ahead/behind before this closeout). Only the manifest below is to be staged; all five original cache files remain untouched. The older current/closeout bullets below are preserved historical records, not the current push status
- Milestone 5C-2 implementation and acceptance are complete in the actual project, based on local `454d7939c3a730a47399b50431ab1e1850a87676`. This **13-file closeout** changes frontend/check tests and this document only. Final frontend tests **770 passed**, lint, typecheck, Webpack build and whitespace checks passed. Ten individually opened screenshots and a sanitized receipt are in the stable external `5c-2` directory below. User explicitly authorizes normal commit/push after acceptance; `git fetch origin main` confirmed no divergence (three preceding local commits ahead, zero behind before this closeout). The push bundle includes those existing 5B-2, 5B-3 and 5C-1 commits unchanged, plus this round; no force push/reset/amend. Earlier closeout bullets below describe their historical, then-unpushed state, not a claim that their earlier rounds performed a push
- Milestone 5C-1 is accepted and included in this **local-only 17-file closeout**, with commit message `feat: add Shanghai weather forecasts and daily travel hints`. Its verified parent is `01224f166a5ff11057b4d522b6be0f4c579b877d` (local 5B-3). This adds one local commit, leaving `main` three commits ahead / zero behind the existing `origin/main` tracking record; no fetch, reset, amend or push. Only current closeout documentation changed in this round; no business code or full-suite rerun. The five original caches and stable evidence remain unchanged. Implementation tests, browser observations and limitations below remain historical results, not new closeout runs
- Milestone 4B was committed/pushed as `0eeba167766216ffbaecfa4610c83539ae36577d`; 4C-1 was subsequently committed/pushed as `84f0eb17f618a13b01e5e16f4b906c32ac1c5e9b` (`feat: add real walking routes between bound activities`). Both acceptance records remain below
- Milestone 4C-2 was reviewed, committed and pushed as `cb1a2605af312b9c8dcb319358615691ca56b0be` (`feat: add real transit routes between bound activities`). Its implementation, supplemental activity→Marker acceptance and historical limitations remain below. This is the verified starting HEAD for 5A-1; the five pre-existing Python cache changes remain excluded from this work
- Milestone 5A-1 was reviewed, committed and pushed as `58cecc300201bf83785a69f520a9b525a1e19eb4` (`feat: confirm accommodation and must-visit places`). Its implementation/acceptance evidence remains below. This is the verified starting HEAD for 5A-2
- Milestone 5A-2 was reviewed, committed and pushed as `3ed0c44e4a3ec1136161cb275c56947d4f15845b` (`feat: prepare interest-based place candidates`), the verified baseline for 5B-1. Its 15-file closeout did not change business code or rerun the implementation suite. Historical evidence and limitations below remain intact: its 12-second search deadline excludes brief cancellation finalization, browser acceptance did not individually trace upstream HTTP attempts, and optional candidates did not participate in scheduling at that milestone
- Milestone 5B-1 was reviewed, committed and normally pushed as `e917000c140e3f9fedb3a3b9adaa17b03fb75244` (`feat: add fixed-order walking schedule previews`), the verified starting HEAD for 5B-2. Its 17-file closeout changed only current documentation status, not business code, and did not rerun its recorded 210 backend / 498 frontend tests. Historical acceptance below remains intact, including the then-uncommitted handoff. Its batch-local pacing, deadline/cleanup distinction and unverified six-place live full-load limit still apply
- Milestone 5B-2 was locally committed as `16e68ff8d02ef698bcf472c1395b455aaba1bc0b` (`feat: add optional candidates to walking schedule previews`), the verified baseline for 5B-3. It has **not been pushed**: at the start of 5B-3 local `main` was one commit ahead of the existing `origin/main` tracking record; no fetch, reset or push was performed. Its 19-file closeout did not change business code or rerun the suite; backend 240 / frontend 558 tests and 120 controlled cross-language cases remain historical implementation results. Historical browser evidence and limitations remain intact
- Milestone 5B-3 implementation, code review and minimal evidence recheck are accepted and included in this **local-only 17-file closeout**, with commit message `feat: add transit reference mode to schedule previews`. Source/test hashes match the accepted version; only current closeout documentation changed, so no full test/lint/typecheck/build rerun. Backend 262 / frontend 622 tests, lint, typecheck, Webpack build and 300 cross-language outputs plus 1,494 rejected mutations remain the recorded implementation-round results, not new closeout runs. Real bus-reference roundtrips, optional tail addition/fallback, mode isolation, 400px layout and map/Marker observations remain below. The historical Oriental Pearl `data_error` limitation is retained without a walking fallback. All five original Python cache contents remain unchanged and excluded
- After this local closeout, `main` is two commits ahead of the existing `origin/main` tracking record (5B-2 plus 5B-3), with no push, amend or reset. Only the 17 listed delivery files are included; staged manifest, whitespace and sensitive-information checks are part of closeout. Original unavailable-evidence records, the new minimal recheck and all limitations remain intact. The nine screenshots and sanitized receipt stay outside Git at `/Users/zijing/projects/trippilot-evidence/5b-3/`, unchanged; nothing was deleted or moved back to temporary storage
- 5B-3 evidence-access follow-up (2026-10-09): original temporary artifacts could not be recovered. A **new minimal Chrome recheck**, not restoration of old evidence, produced nine verified screenshots and a sanitized receipt at `/Users/zijing/projects/trippilot-evidence/5b-3/`. Only PROGRESS changed in the repository; no business changes or automated-suite rerun. See the follow-up table below; original observations and data-error limits remain historical records
- GET /places/search accepts keyword + city and maps them to AMap v5 keywords + region with city_limit=true, page 1 and page size 20
- Place returns id, name, nullable address/category, validated latitude/longitude, and source="amap"; invalid POIs are skipped without fabricating coordinates
- Missing key returns 503, upstream timeout 504, other upstream failures or malformed payloads 502, invalid query parameters 422, and empty valid results 200 with []
- The result page supports real Place search/preview and user-confirmed activity bindings. POST /trips/plan now accepts/echoes optional structured confirmed requirements; its Mock activities, transport, weather and costs remain unchanged. Requirement POIs do not automatically bind activities or change the example plan; activity bindings are still separate result-local React state
- Legacy POST /trips/plan still uses a fixed two-day activity template for valid 1-3 day requests; dates are aligned to the requested start date and the validated request is echoed for the overview. The separate walking/transit reference preview uses every actual requested date instead
- A notice explains when the requested duration differs from the two-day example; the fixture is not optimized for people, budget, preferences or daily time constraints
- Activity and transport costs reconcile to a fictional CNY 460 total for all travelers (transport 40, food 240, tickets 100, other 80); remaining or exceeded budget is derived from the submitted budget
- Weather, original transport, costs and activity times remain clearly marked Mock. Map numbers identify search candidates or deduplicated daily bound POIs. One selected walking/transit scheme appears only after a successful explicit real query, and only in the daily bound view. Real estimates do not validate or overwrite Mock information. Replanning remains disabled
- Search is explicitly submitted, fixed to Shanghai, and uses GET /places/search. Candidate/marker selection only previews; activity binding requires a separate “确认绑定”. Each new binding session clears candidates/selection, and abort/request ID/context checks prevent stale requests from affecting a different activity/day/result
- Bindings are keyed by day number + date + activity ID, retained across day switches, and removed on editing/regenerating or refreshing. Multiple activities may share one POI marker; unbinding one activity preserves the others. No localStorage, database or server persistence is used
- SDK loading is shared and browser-only. Shanghai's center is obtained via DistrictSearch before map construction; no hardcoded POI coordinates, IP/GPS initialization or geolocation plugin is used
- Map creation is independent of candidate updates; search cancellation/request IDs prevent stale results, and unmount cleans listeners, markers, map and late async results
- Only GET /_AMapService/v3/config/district is proxied to https://restapi.amap.com/v3/config/district, limited to Shanghai/province/subdistrict=0/extensions=base/page=1. No catch-all proxy, custom-style endpoint, POI bypass, routing, weather or IP endpoint is allowed
- serviceHost is set before SDK loading. Every supplied JS Key must match backend configuration; jscode is appended only by the backend. Identical, individually validated key/s copies from the real SDK are folded; other duplicate/unknown parameters are rejected. Known SDK diagnostic fields are validated and stripped. Client security-code overrides, unsafe paths/callbacks, redirects and sensitive upstream responses are still rejected; TLS verification and the existing explicit CORS origins are retained
- 4C-1 added an independent walking endpoint; 4C-2 added an independent Shanghai transit endpoint and result-local mode selection. Those milestones added no scheduling or real weather. 5C-1 now adds independent real weather only. No driving, LLM/Agent, database, global state manager or new browser dependency has been added; backend SDK proxy restrictions and credentials remain unchanged
- POST /routes/walking accepts origin/destination POI IDs and finite longitude/latitude, uses the fixed AMap walking upstream and returns source, UTC query time, status and normalized meters/seconds/coordinate segments. Only the first upstream proposal is validated and used; an invalid first proposal fails safely. No straight-line or Mock fallback exists
- Walking queries follow original activity adjacency, not a filtered list of bound activities. Missing endpoints and same-place pairs do not trigger a route request. Replacement/removal of an endpoint, day switch, edit/regeneration and unmount invalidate route state and pending requests; day bindings themselves remain preserved
- POST /routes/transit reuses validated endpoint coordinates, fixes both cities to Shanghai and calls only AMap v3 transit/integrated. It selects the first complete supported valid proposal, and one valid supported alternative per ride; it does not claim the fastest/optimal route. Mode switches invalidate both modes without automatically querying. Trip dates/Mock activity times are not sent as real departure times
- POST /places/candidates accepts only confirmed accommodation, confirmed must-visits and supported interests. It aggregates fixed Shanghai keyword searches on explicit request, preserves required POIs, returns at most 18 optional POIs and reports each query plus overall success/partial/failed status. The independent whole-trip candidate pool supports local exclusion/restoration; 5B-2 explicitly submits its first three eligible optional POIs to the separate walking preview. It still does not perform semantic avoid-place filtering
- POST /trips/schedule-preview accepts confirmed accommodation, up to six required and three distinct optional POIs, requested dates/daily window, separate stay/lunch settings and `transport_mode` (`walking` by default, or `transit`). Every edge and movement uses the selected mode, without comparison, mixing or fallback. Required visits retain their original-order, same-mode/same-batch baseline; only when they all fit (or none were requested) may one optional be appended to each day's fixed tail. Candidate/rule/mode changes synchronously invalidate the preview; old Mock plan/budget/weather, activity bindings and map routes remain independent

## Next

- **Current instruction: stop after ordinary 6C-1 commit/push.** No next stage, new Qwen allowance, formal-draft map, autosave, schema/dependency/configuration change or optimization is included. The earlier stage stopping points below are historical and superseded by explicit 6C-1 authorization.
- **Current instruction: stop after ordinary 6B-2 commit/push.** Do not start a subsequent stage. The earlier 6B-1 stopping point below is retained as history and was superseded by the explicit 6B-2 authorization. No new Qwen allowance, archive update/delete, autosave, persistence of working copies or multi-user access is introduced.
- Close out only this **6B-1** delivery with an ordinary stage-only commit/push after remote and staged-content checks. Stop afterward; do not start history editing (6B-2). No further Qwen call is authorized by the exhausted **20 / 20** allowance. Physical-phone/touch, dedicated live subway/transfer-operation samples, broader real upstream fault acceptance, six-required-plus-three-optional live full-load acceptance and the Turbopack limitation remain separate; historical evidence is retained
- Review existing dependency security advisories as a separate, approved maintenance task before deployment

## Milestone 6C-1 Unified Formal Planning Workflow (2026-10-12)

### Baseline, paths and state boundaries

- Actual checkout `/Users/zijing/projects/trippilot`; starting HEAD **`1a25cff0b6809699bcd8572560eeddab00974784`** and freshly fetched `origin/main` matched (0 ahead/behind). Read root/frontend instructions, project context, current progress and installed Next.js client/page documentation. No temporary implementation copy, reset or unrelated overwrite. The five original cache SHA256 values remain unchanged.
- `/`: genuinely empty requirement form → existing strict local TripRequest confirmation → `newWorkspaceInputs` → `EditablePlanningSession` → `PlanningWorkspace`. **No `/trips/plan`, constructed TripPlan or fake initialRequest** in the formal path; no automatic candidates, routes, weather or model requests. Explicit Place preview/confirmation and strict request validation remain.
- `/plan/from/[id]`: validated historical inputs use the **same** editable session and workspace. `origin.kind` explicitly distinguishes new from historical, rather than inferring provenance from an initialization object. Source reference remains read-only; its response is never installed as new official success. A fresh valid generation is still required before another save.
- `EditablePlanningSession` extracts the 6B-2 coordinator, preserving synchronous current-session guards, whole-input conversion before invalidation, deep-copy initialization and keyed revision changes. Cancel leaves the mounted workspace intact; confirmation reuses `applyEditedRequirements` for compatible stay/lunch/candidate/fee retention and cleanup. Date/people changes clear fees; changed accommodation/required/interests clear incompatible candidates/exclusions. No alternate reconciliation or scheduling algorithm.
- `defaultScheduleSettings` is shared by new initialization and existing schedule hook defaults. Existing capture/ref/version/AbortController and adoption/save paths stay in place. Display is organized into native disclosure sections: **需求与地点准备 → 生成与查看草案 → 核对、调整与保存**. Navigation opens its destination and focuses/scrolls the summary; hidden contents remain mounted, including frozen uncertain-save state, costs, explanation and preview controllers. Weather/manual and natural-language preview links and preview target are in the same review disclosure, so a visible link does not lead to a hidden target.
- Status distinguishes no valid draft, loading and valid complete/partial/unarranged results. Required-only inputs can generate immediately; optional-only inputs require explicitly prepared eligible candidates. Weather/AI/adjustment are optional, not generation/save prerequisites. Removed unavailable-replanning placeholder; clarified budget is for checking, pace is not a scheduling input, AI does not generate the whole itinerary.
- `/demo`: independent new-tab footer entry preserving the old **combination/integration example**, `/trips/plan`, Mock activities/budget/weather and real activity-binding map. Existing formal tools also remain on that legacy combination page for compatibility, clearly labeled as example tooling; none of its Mock data/map is mounted on the normal homepage or historical workspace. Opening `/demo` itself makes no business query. This is **not** a new formal-draft map.
- Backend, saved-trip-v1, SQLite schema, keys, model settings, CORS/proxy, fee arithmetic and scheduling rules are unchanged. No new dependency or persistence. [3–5 minute demo script](DEMO_6C1.md) explains the normal user path and AI/data/rule responsibilities.

### Actual checks in this round

| Command / check | Result |
| --- | --- |
| `cd frontend && npm test -- --reporter=dot` | **1,020 tests / 49 files passed**, including nine new real-component composition cases and all 1,011 baseline cases |
| `cd frontend && npm run lint` | Passed, no warnings |
| `cd frontend && npm run typecheck` | Passed, including new `/demo` route |
| `cd frontend && npm run build -- --webpack` | Passed on final business code; routes `/`, `/demo`, `/plan/from/[id]`; existing font network permission, no configuration change |
| Python → TypeScript compatibility inside full frontend tests | Existing disk-backed archive exporter ran with `-B` on temporary databases; all 12 saved-trip-v1 cases still validate/restore, with other existing contract suites preserved |
| `git diff --check`; cache/manifest/sensitive-content checks | Passed; only stage delivery files are eligible for staging |

- No backend source change, so no unrelated full backend suite rerun. Historical counts are not claimed as new runs. The existing Starlette TestClient/httpx deprecation warning remains in controlled exporter checks.
- New tests exercise actual blank form and Place confirmation, no Mock/automatic calls, default independence/strict refusal, required-only and optional-only generation, shared cancel/confirm cleanup, old success/error/finally after confirmation, navigation preserving draft/fees/AI/preview, and uncertain save retry retaining **identical operation ID and frozen content even after fee changes**. Existing history ABA, adoption, costs, model, weather and map regressions remain. Six old flow-test imports now explicitly target `DemoPlanner`; the weather layout assertion now targets its independent review section instead of the superseded pre-candidate layout. No existing business assertions were deleted.
- Early runs identified a superseded page-order assertion and new test-only locator/event mistakes; fixed before final passing checks, without relaxing business contracts. AI preservation is explicitly **controlled model output**, not a real model acceptance claim. This round Qwen **0**; inherited cumulative allowance stays **20/20**.

### Real production Chrome evidence

- Chrome **155.0.8059.39**, real UI/POIs/routes/weather; reused backend PID42730. Only the verified project frontend production process was restarted to load Webpack builds. Final production homepage/history/demo were also opened after final wording build. No raw network log or database exported; a tool-generated console file was summarized then removed rather than delivered. Demo console had **0 errors / 1 warning**, and recorded local business requests all returned200.
- Actual new request: Shanghai **2026-10-12**, 2 travelers, budget3000,09:00–18:00, 摄影, confirmed 静安寺 (南京西路1686号) accommodation and 武康大楼 (淮海中路1850号) required. Entering workspace produced **only the two explicit POI searches**; no `/trips/plan`, candidate, schedule, weather or model call. No map container/Mock plan on formal path, save initially disabled.
- Explicit required-only walking generation succeeded:09:00–09:36 outbound (**2156s →36min**), required09:36–10:36, return **2153s →36min**, accommodation11:12. Optional count0; no candidate/weather prerequisite.
- Explicit 摄影 candidate retrieval returned18 optionals. A second generation used first-three inputs and added 延中广场公园 at13:00–14:00 after an explicit11:35–12:00 wait and12:00–13:00 lunch, returned14:44; required remained09:36–10:36. Status was correctly partial for other unarranged optionals. No optimum/complete-demand claim.
- User estimates required0, optional25.50, meals100, lodging500,other0 gave **¥625.50** with walking ticket rule0. Collapsing/reopening draft/review, and Enter-key navigation, retained exact inputs/timeline; request count stayed **5→5**. Checkbox selection also worked by focus/Space. Changing travelers then canceling retained the original; confirming only budget3000→4000 cleared official response but retained compatible inputs/candidates/fees, with **5→5** requests. Explicit third generation restored a valid draft, not an automatic call.
- One real weather query: date2026-10-12, day阴/night多云, source published **22:01:21 Shanghai**, queried22:21:35. Neither triggered rain/snow; preview is a **manual adjustment**, not live rain acceptance. Weather-based explicit preview removed 延中广场公园 and added 辅德里公园 while required time stayed09:36–10:36. Review navigation/cancel preserved official state with **8→8** requests. A second explicit preview was adopted; adoption **9→9** requests. New-place user estimate30 produced official subtotal **¥630.00** after adoption; return14:28. AI was never called.
- Saved **`8bf1ada8099744c9b2937d098603da8b`**, “6C-1 正式入口 · 已采用方案”, server time`2026-10-12T14:22:37.623188Z`, generation`2026-10-12T14:22:13.497917Z`. History opened an independent tab; original dialog/workspace remained. Copy initialization made **one GET detail only**, and saving was disabled until fresh generation.
- In that copy, changed 武康大楼 stay to75/user, explicitly generated fresh walking routes and saved **`3ea9f0532e6d473683a122661245f9e3`**, “6C-1 历史副本 · 75分钟新记录”, server time`2026-10-12T14:23:08.188170Z`, generation`2026-10-12T14:23:03.267328Z`; return14:28 after a shorter pre-lunch wait. Reload restored original source inputs, not unsaved/new-success state, and the new record remained readable. A separate read-only check found the complete original record unchanged, including saved/generated/query times; canonical snapshotSHA256 **`6ca0e450ef244594601fdfe8845357b5b68b1503a42ac58a4985b3a2e9184e55`**. Existing records were not edited/deleted.
- `/demo` opened with zero business calls; explicit form submission called the retained Mock interface. Bound original adjacent activities to real 静安寺/武康大楼 and explicitly queried walking. Actual AMap roads, place labels, attribution, two markers and blue real route were visible. Activity selected/centered/highlighted Marker1; clicking the visible Marker2 content selected/scrolled its activity and reset Marker1. An initial click on the SDK outer wrapper did not select and was **not** counted as passing; visible-marker click and inspected replacement screenshots establish the result. Fit route/Marker interactions added no business calls. Formal path and demo each fit400px (scrollWidth400); history also readable. No hardware phone/touch claim.
- Safe **local** counts: formal tab `GET /places/search`2, `POST /places/candidates`1, `POST /trips/schedule-preview`5 (3 ordinary+2 previews), `POST /weather/forecast`1, save1, history list1, detail2 (one UI+one immutable-record diagnostic). Copy: detail1 on open, schedule1,save1; after reload detail1,list1,new-detail1. Demo: POI3,Mock-plan1,single-walk1. Final fresh homepage0. These are not individually observed AMap upstream attempts. Full receipt below records boundaries; no increased upstream budgets/retries.

Stable evidence directory: `/Users/zijing/projects/trippilot-evidence/6c-1/`, **outside Git**. Every listed screenshot was opened and visually checked:

| Evidence | Observation |
| --- | --- |
| [01-new-workspace.png](/Users/zijing/projects/trippilot-evidence/6c-1/01-new-workspace.png), [02-required-only.png](/Users/zijing/projects/trippilot-evidence/6c-1/02-required-only.png) | New origin, no automatic draft, true required-only walking |
| [03-candidate-draft.png](/Users/zijing/projects/trippilot-evidence/6c-1/03-candidate-draft.png), [04-preserved-budget.png](/Users/zijing/projects/trippilot-evidence/6c-1/04-preserved-budget.png), [05-confirm-edit-invalidates.png](/Users/zijing/projects/trippilot-evidence/6c-1/05-confirm-edit-invalidates.png) | Optional/lunch/return, retained user costs, transactional edit |
| [06-adjustment-preview.png](/Users/zijing/projects/trippilot-evidence/6c-1/06-adjustment-preview.png), [07-adopted-budget.png](/Users/zijing/projects/trippilot-evidence/6c-1/07-adopted-budget.png) | Actual before/after places and adopted shared cost calculation |
| [08-official-saved.png](/Users/zijing/projects/trippilot-evidence/6c-1/08-official-saved.png), [09-readonly-history.png](/Users/zijing/projects/trippilot-evidence/6c-1/09-readonly-history.png), [10-copy-source.png](/Users/zijing/projects/trippilot-evidence/6c-1/10-copy-source.png), [11-copy-new-draft.png](/Users/zijing/projects/trippilot-evidence/6c-1/11-copy-new-draft.png) | Persisted formal record, explicit new-tab copy, fresh75-minute generation |
| [12-mobile-workspace.png](/Users/zijing/projects/trippilot-evidence/6c-1/12-mobile-workspace.png), [13-mobile-budget.png](/Users/zijing/projects/trippilot-evidence/6c-1/13-mobile-budget.png) |400px navigation and formal budget |
| [14-demo-real-map-route.png](/Users/zijing/projects/trippilot-evidence/6c-1/14-demo-real-map-route.png), [15-activity-marker.png](/Users/zijing/projects/trippilot-evidence/6c-1/15-activity-marker.png), [16-marker-activity.png](/Users/zijing/projects/trippilot-evidence/6c-1/16-marker-activity.png), [17-marker-selected-card.png](/Users/zijing/projects/trippilot-evidence/6c-1/17-marker-selected-card.png), [18-mobile-demo-map.png](/Users/zijing/projects/trippilot-evidence/6c-1/18-mobile-demo-map.png) | Independent real map, fitted walking line, both selection directions,400px |
| [19-mobile-history-after-reload.png](/Users/zijing/projects/trippilot-evidence/6c-1/19-mobile-history-after-reload.png), [20-final-home-mobile.png](/Users/zijing/projects/trippilot-evidence/6c-1/20-final-home-mobile.png), [receipt.json](/Users/zijing/projects/trippilot-evidence/6c-1/receipt.json) | Final production history/new homepage and sanitized operation/count record |

### Delivery manifest — 26 files

1. `frontend/src/app/demo/page.tsx` — independent labeled legacy entry.
2. `frontend/src/app/page.tsx` — truthful homepage flow and demo link.
3. `frontend/src/components/trip-plan-result.tsx` — explicit demo provenance.
4. `frontend/src/components/trip-request-form.test.tsx` — retain legacy confirmation regression.
5. `frontend/src/components/trip-request-form.tsx` — empty local-confirm mode, correct submit text.
6. `frontend/src/components/trip/candidate-preparation.test.tsx` — explicit legacy lifecycle fixture.
7. `frontend/src/components/trip/candidate-preparation.tsx` — updated edit/retention explanation.
8. `frontend/src/components/trip/confirmed-places.tsx` — explicit new/history/demo explanations.
9. `frontend/src/components/trip/demo-planner.tsx` — moved legacy form/Mock integration flow.
10. `frontend/src/components/trip/editable-planning-session.tsx` — shared transaction/session coordinator.
11. `frontend/src/components/trip/formal-planning.integration.test.tsx` — nine new composition cases.
12. `frontend/src/components/trip/planning-workspace.tsx` — stable sections, navigation and status.
13. `frontend/src/components/trip/restored-trip.tsx` — reuse shared session from history.
14. `frontend/src/components/trip/schedule-budget.tsx` — accurate formal-path fee lifecycle copy.
15. `frontend/src/components/trip/schedule-preview.test.tsx` — preserve explicit legacy regression.
16. `frontend/src/components/trip/schedule-preview.tsx` — updated lifecycle and independent-map wording.
17. `frontend/src/components/trip/transit-route.test.tsx` — retain legacy transit regression.
18. `frontend/src/components/trip/trip-planner.test.tsx` — retain legacy binding/reset regression.
19. `frontend/src/components/trip/trip-planner.tsx` — direct validated new workspace, no TripPlan.
20. `frontend/src/components/trip/walking-route.test.tsx` — retain legacy walking regression.
21. `frontend/src/components/trip/weather-forecast.integration.test.tsx` — new review-section layout assertion.
22. `frontend/src/lib/restore-trip.ts` — independent new input initialization and shared reconciliation.
23. `frontend/src/lib/schedule-input.ts` — shared default settings.
24. `frontend/src/lib/use-schedule-preview.ts` — consume same defaults, no algorithm change.
25. `docs/DEMO_6C1.md` — concise walkthrough.
26. `docs/PROGRESS.md` — this record, historical evidence/limits preserved.

### Limits and closeout

- No core acceptance blocker remains. Stage-only ordinary commit/push is authorized; final exact-manifest, whitespace, configured-secret, five-cache hash and remote checks passed (0 ahead/behind before the new commit). Closeout must contain only the 26 files above and stop afterward. No force push/reset, unrelated staging, environment/evidence/DB inclusion or cache changes.
- Full live acceptance this round is **walking**, not dedicated real bus/subway/transfer or fault acceptance. Transit/AI/errors/ABA/uncertain writes are controlled regression coverage; no live model or induced real fault. Budget examples are user inputs, not verified prices. Native keyboard/400px responsive checks are not a physical-phone or full accessibility certification.
- Existing source freshness/operating-service/geometry/data_error limits, six-required-plus-three-optional live full-load gap, batch-local throttling and deadline-versus-cancellation-cleanup distinction remain. No formal-map or budget optimizer. Turbopack limitation and dependency advisories below remain; no unrelated remediation.

## Milestone 6B-2 Continue Planning from a Historical Snapshot (2026-10-12)

### Baseline and implementation boundaries

- Actual repository `/Users/zijing/projects/trippilot`; HEAD and freshly fetched `origin/main` both **`45a14edcee1463188925a0dbe99f20853186eedd`**, zero ahead/behind before closeout. Existing changes were only the five original Python caches. Their SHA-256 contents still match the starting record. No reset, temporary project copy, dependency, backend/schema/DB-table change, key/environment/proxy/CORS/model configuration change or Qwen request.
- History detail adds a normal `target="_blank"` / `noopener noreferrer` link to `/plan/from/[id]`. The original tab remains mounted and unchanged. The dynamic route follows the installed Next.js async-params documentation; relevant local client-component/page documentation and frontend instructions were read before implementation.
- `restoreSavedTrip` first applies the existing strict `isSavedDetail`/v1 contract, then separately deep-copies the read-only source and complete working inputs. Requirements include confirmed places/order, dates, budget, travelers, pace/interests, avoid text and daily window. Settings retain exact transport mode, each stay's minutes and `default/user` source, and lunch enabled/start/end. Valid disabled lunch clocks are preserved; the existing fallback for an invalid disabled UI draft remains. Raw fee strings are not parsed into replacement values during recovery.
- **No historical response is installed in schedule success state.** `PlanningWorkspace` is shared by normal new trips and restored copies and takes a `TripRequest` plus optional explicit input initialization. The Mock/map example remains a separate child in the normal flow; restoring or confirming edited requirements does not call `/trips/plan` and does not fabricate a TripPlan. A collapsible `SavedTripDetails` is read-only reference with original saved/generated/query times.
- Only explicit `querySchedulePreview` success through its existing strict validator becomes a new official result. Failure preserves current inputs and history, not a historical-success fallback. The existing capture/save projection, transactional unique operation ID, frozen retry, response validation and `POST /saved-trips` are reused unchanged in meaning. New saves create new rows; source ID is contextual UI information, not a new database parent/version relationship.

### Historical candidates, editing and fee rules

- The internal candidate snapshot has an explicit **`history` provenance** containing original record identity/save time and at most the three original `schedule.request.optional_places`, in original order. `response`/`lastAttempt` are null: **no synthetic CandidateResponse**, keywords, result counts or query timestamps. Original unarranged optionals are not inferred to be excluded. There is no reconstruction of the former full pool, prior exclusions, weather, AI/natural-language intent, exposure labels, previews, bindings or map selection.
- `candidateItems` is the shared accessor used by candidate UI, schedule selection and adjustment preparation/authorization. Restored optionals support the existing local exclude/restore and atomic preview/adoption flow. An explicit new search success/partial replaces history as one batch; all-failed/network failure retains the restored list and copy-local excluded IDs. Exclusions continue to be remembered by ID. The UI warns that an older, unarchived exclusion can reappear in a new search.
- The same requirement form has a local-confirm mode with existing validation and real Place picker. Opening/canceling edit keeps the mounted workspace and pending/current state; valid confirmation converts all new inputs first, synchronously invalidates the old workspace and remounts one complete new input seed. No half-initialized publish or default-setting effect. Search/selection remains explicit and cancel never commits a preview Place.
- Accommodation snapshot, ordered must-visits or interests changing clears optional history/pool and exclusions. Date/people/budget/window-only edits keep compatible place inputs. Stay settings/source are retained only for still-relevant IDs; new IDs default to 60/default. Mode and lunch are retained; an enabled lunch incompatible with the new daily window blocks generation with a correction/disable prompt, never shifts silently.
- Confirmed **date range or traveler count** changes clear all user estimates. Accommodation changes clear lodging; otherwise retain relevant ticket IDs and exact meal dates plus whole-trip other/lodging values. Blank, explicit zero and invalid text retain their meaning. No scaling, date-index meal remapping, room-price/overnight inference or new quotation claim. Before a fresh draft, input-only fee rows permit editing but show no budget conclusion; after success, the existing cost calculator and adjustment comparison are reused.
- A copy gets a fresh random session identity on every successful restore, even for the same record. Loader abort + monotonically increasing read attempt, immediate source render invalidation and keyed session/workspace revisions protect source A→B→A and old responses. Synchronous refs guard edit confirmation, current schedule capture, pool/settings/cost input and save operations; unmounted candidate/schedule/save callbacks cannot start new work. Old success/error/finally cannot install another session's draft. No storage/global state framework added.
- Weather, candidates, explanation, natural-language preview and adjustment hooks are the existing workflow, mounted without automatic business requests. Fresh results can use them only through their existing explicit actions. A restored edit confirmation/remount clears dependent reports, annotations and previews. Cost changes remain local and do not invalidate weather/adjustment/AI or query routes. Copy changes trigger `beforeunload` protection; this protects ephemeral page work even after a snapshot save, not a claim of autosave. Refresh reloads the original archive inputs and discards copy edits.

### Checks actually run in this round

| Command | Actual result |
| --- | --- |
| `cd frontend && npm test -- --reporter=dot` | **1,011 tests / 48 files passed**, including 31 added cases and all 980 baseline cases |
| `cd frontend && npm run lint` | Passed, no warnings |
| `cd frontend && npm run typecheck` | Passed, including `/plan/from/[id]` generated types |
| `cd frontend && npm run build -- --webpack` | Passed on final business code; existing Google Fonts network permission used, no build configuration changes |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -p test_saved_trips.py -v` | **9 passed**, actual temporary SQLite initialization/reopen, concurrency/idempotency, rollback, safe errors and strict content checks; backend source unchanged, full backend suite **not** rerun |
| Python → TypeScript archive compatibility | Existing `tests/export_saved_contract.py` executed with `-B` in tests, all **12 disk-backed business cases** accepted and restored to the same schedule request, including walking/transit, only-optional, no optional, partial/unscheduled and blank dates |
| `git diff --check`, cache hashes, explicit manifest/security check | Passed; cache/environment/DB/sidecar/evidence excluded |

- Added pure restoration/edit tests, loader race tests, historical candidate/schedule/weather-adjustment hook tests and actual restored-page/form/save composition tests. They prove independent deep copies, strict refusal of corrupt/version/foreign references, exact settings, raw fee boundaries, changed home/required/interests clearing, cancel semantics, generation failure retention, no implicit Mock/model/weather/candidate calls, source and request ABA, unmount/old callback isolation, valid partial/all-failed candidate responses, adjustment cancel/adopt and new-only save capture.
- Early development runs exposed test-only locator mismatches and an incorrect mock import path; these were corrected before final passing lint/typecheck/test/build. The original application contracts were not weakened to obtain passing tests. Final UI copy distinguishes confirmed copy inputs from the absent Mock example on that page.

### Real production Chrome acceptance and stable evidence

- Actual Chrome **155**, not a mocked browser/map. Reused existing backend **PID 42730** and its database; only the verified project frontend production process was restarted for builds. No backend restart was needed in this stage. The real 6B-1 v1 records were readable by the modified frontend without migration or rewriting.
- Source **`83430e1750184a4cab09bf9910350bb6`** (6B-1 adopted walking draft) opened via history link in a new tab. Original tab retained its open history dialog and the manually entered budget `3456`. Restoration made exactly **one GET source detail (200)**, with **zero** candidate/schedule/POI/weather/model/Mock-plan requests; mode walking, four 60-minute default stays, 12:00–13:00 lunch, three saved optional POIs/order and raw fee inputs were checked. New formal save was disabled.
- Edited required 武康大楼 stay to **75/user** and lunch to **12:15–13:15**; only clicking generate requested fresh real walking data. Used edges **2156s→36min**, **3056s→51min**, **1650s→28min**. Required visit **09:36–10:51**, arrival at 辅德里公园 **11:42**, explicit waiting until12:15, lunch12:15–13:15, optional visit13:15–14:15, accommodation return **14:43**. The valid result remained `partial` because only one of three optionals is appended per day, not because required visits failed.
- Changing travelers and canceling preserved the current timeline. Confirming **2→3 travelers** cleared all seven editable fee fields and official result, retained75-minute stay/lunch/candidates and made **no additional request**. A second explicit fresh generation succeeded. Required ticket `0`, other `bad`, and remaining blank fee inputs retained distinct semantics in the new saved record.
- Explicit real weather query passed. Both day/night did not trigger precipitation; the subsequent preview was a **user's manual choice**, not a claimed rain event. Restored-candidate preview replaced 辅德里公园 with 静安雕塑公园 without changing the official result or exclusions. Saving while the preview was ready captured the **original formal response**, not the preview. Cancel added no request and retained the formal draft. Adoption of restored candidates is covered by controlled shared-hook tests; no extra live adoption or Qwen call is claimed.
- New row **`b6b42423e90f4dd49922b71a5ebdcb53`**, “6B-2 独立副本 · 3人75分钟”, saved **`2026-10-12T11:44:46.194235Z`**, generated **`2026-10-12T11:42:56.861430Z`**. After reload, copy inputs returned to the old source60-minute/12–13 settings with no valid formal draft; history still displayed the new3-person/75-minute/lunch12:15–13:15/14:43-return record with its original query times. Neither save nor history read queried external services.
- Full snapshot SHA256 using the frontend's recursively sorted JS canonical JSON still matches both prior 6B-1 receipts: `f206276392794235bd13d7aece36900d` → `ec204b217c3e83ab07ff9b5f1e714137c638b65b1db1b85be13ee6e50a8c7fa2`; source `83430e1750184a4cab09bf9910350bb6` → `e84d5052fcee6470e4046167b6b4fe3f6bea477100871d0c39df442f6165e1cd`. Original saved/generated/query times are unchanged. New snapshot hash `9718fd3d7c3a8478f3b741e31f15967c6b0f1e9f6fd973048265c69d8f8168d6`. No DB or full snapshot was exported as evidence. An initial Python JSON hash diagnostic used different numeric serialization and was not treated as an archive mutation; same-algorithm JS checks match.
- Normal new flow still confirmed a real home POI and explicitly posted the Mock plan. Two original adjacent Mock activities were independently bound to 静安寺/武康大楼, followed by a real **2.69km /36min** walking request. Actual AMap roads/labels/copyright and blue geometry were visible. Activity click centered/highlighted Marker1; clicking Marker2 scrolled/selected its activity and reset Marker1. Fit-all retained the route and did not query again. At **400px**, copy settings/history/map fit with document scroll width400. Phone hardware/touch is not certified.
- Safe local counts are recorded separately by phase in [receipt.json](/Users/zijing/projects/trippilot-evidence/6b-2/receipt.json): copy actions **3 schedule requests** (2 ordinary +1 preview), **1 weather**, **1 save**; normal-flow regression **3 POI searches**, **1 Mock plan**, **1 single walking route**; additional archive-only reads include restoration/list/detail/reload and **6 read-only hash diagnostics**. Business responses returned200. These are **local request counts**, not individually observed AMap upstream attempts. Qwen **0**, cumulative **20/20** unchanged.

All screenshots below were **opened and visually inspected**, not merely written. Stable directory `/Users/zijing/projects/trippilot-evidence/6b-2/`, outside Git:

| Evidence | Observed result |
| --- | --- |
| [01-source-history.png](/Users/zijing/projects/trippilot-evidence/6b-2/01-source-history.png), [02-independent-inputs.png](/Users/zijing/projects/trippilot-evidence/6b-2/02-independent-inputs.png), [03-copy-source.png](/Users/zijing/projects/trippilot-evidence/6b-2/03-copy-source.png) | Read-only source/new-tab entry and input-only workspace; original timestamps and copy limitations |
| [04-history-candidates.png](/Users/zijing/projects/trippilot-evidence/6b-2/04-history-candidates.png) | Three saved optional POIs in order, explicit historical provenance, no invented retrieval metadata |
| [05-new-walking-timeline.png](/Users/zijing/projects/trippilot-evidence/6b-2/05-new-walking-timeline.png) | Fresh route estimates,75-minute required stay, lunch/wait and lodging return |
| [06-people-cleared-estimates.png](/Users/zijing/projects/trippilot-evidence/6b-2/06-people-cleared-estimates.png), [07-mobile-settings.png](/Users/zijing/projects/trippilot-evidence/6b-2/07-mobile-settings.png) | Confirmed traveler change clears raw fees; retained settings and400px layout |
| [08-restored-adjustment-preview.png](/Users/zijing/projects/trippilot-evidence/6b-2/08-restored-adjustment-preview.png), [09-new-record-saved.png](/Users/zijing/projects/trippilot-evidence/6b-2/09-new-record-saved.png) | Real restored-candidate preview and separate save of formal, not unadopted result |
| [10-new-history-detail.png](/Users/zijing/projects/trippilot-evidence/6b-2/10-new-history-detail.png), [11-refresh-clears-copy-edits.png](/Users/zijing/projects/trippilot-evidence/6b-2/11-refresh-clears-copy-edits.png), [12-mobile-new-record-after-refresh.png](/Users/zijing/projects/trippilot-evidence/6b-2/12-mobile-new-record-after-refresh.png) | Persistent new archive, ephemeral copy reset, final production wording and narrow read-only history |
| [13-real-map-walking-route.png](/Users/zijing/projects/trippilot-evidence/6b-2/13-real-map-walking-route.png), [14-activity-selects-marker.png](/Users/zijing/projects/trippilot-evidence/6b-2/14-activity-selects-marker.png), [15-marker-selects-activity.png](/Users/zijing/projects/trippilot-evidence/6b-2/15-marker-selects-activity.png), [16-mobile-map-regression.png](/Users/zijing/projects/trippilot-evidence/6b-2/16-mobile-map-regression.png) | Real basemap, route, bidirectional markers and400px route fit; no additional business requests |

### Delivery manifest — 23 files

1. `frontend/src/app/plan/from/[id]/page.tsx` — new input-copy route.
2. `frontend/src/components/trip-plan-result.tsx` — separate Mock/map example from shared real workspace.
3. `frontend/src/components/trip-request-form.tsx` — populated local-confirm/cancel mode and unmount guards.
4. `frontend/src/components/trip/candidate-preparation.tsx` — history provenance and copy lifecycle text.
5. `frontend/src/components/trip/confirmed-places.tsx` — copy-specific confirmed-input explanation.
6. `frontend/src/components/trip/saved-history.tsx` — new-tab restore link, reusable read-only reference.
7. `frontend/src/components/trip/schedule-preview.tsx` — history-input provenance/settings copy.
8. `frontend/src/components/trip/planning-workspace.tsx` — shared input-initialized workflow and fee editing.
9. `frontend/src/components/trip/restored-trip.tsx` — copy source, guarded edit/apply/cancel and leave protection.
10. `frontend/src/components/trip/restored-trip.integration.test.tsx` — actual page/form/generation/save composition.
11. `frontend/src/lib/candidate-pool-state.ts` — explicit historical source/shared accessor.
12. `frontend/src/lib/restore-trip.ts` — strict deep-copy recovery and pure edit reconciliation.
13. `frontend/src/lib/restore-trip.test.ts` — mapping, fee/lunch rules and cross-language v1 recovery.
14. `frontend/src/lib/restore-trip.test-fixtures.ts` — controlled validated archive fixture.
15. `frontend/src/lib/restored-candidates.test.tsx` — historical pool, failed/partial refresh and adjustment integration.
16. `frontend/src/lib/schedule-adjustment.ts` — reuse candidate accessor for restored targets.
17. `frontend/src/lib/schedule-input.ts` — shared eligible inputs and exact valid lunch clocks.
18. `frontend/src/lib/use-candidate-pool.ts` — atomic input seed, history replacement/retention and callback guards.
19. `frontend/src/lib/use-restored-trip.ts` — cancellable validated loader with unique sessions.
20. `frontend/src/lib/use-restored-trip.test.tsx` — accepted/pending ABA, corrupt records and unmount/retry guards.
21. `frontend/src/lib/use-saved-trips.ts` — mounted-session save/retry guard, existing idempotency retained.
22. `frontend/src/lib/use-schedule-preview.ts` — exact initialization/current input capture and unmount guards.
23. `docs/PROGRESS.md` — this record, original history preserved.

### Limits and stopping point

- Real acceptance used walking with one required and three restored optionals. Transit restoration, only-optional/no-visit/blank-date archives, all-failed/partial candidate refresh, idempotent write races and network/ABA behavior are controlled tests, **not real upstream fault samples**. No new live subway/transfer, full six-required-plus-three-optional load, AI, or physical-phone test. Historical `data_error`, Turbopack/Google Fonts, SDK optional logging and dependency-advisory limitations remain unchanged.
- Input restoration is not POI verification or a promise that historic estimates remain valid. Fresh generation queries only selected route mode. Historical source/candidates/fees are clearly labeled; no reconstruction of missing state, no autosave, no write-back to original rows. Schema remains v1; future migration/authentication/backups and account ownership remain separate work.
- After accepted checks and remote/manifest review, ordinary commit/push of these23 files only is authorized. Stop at6B-2. Caches, `.env` files, DB/sidecars, generated artifacts and the external16 screenshots/receipt are never part of the commit.

## Milestone 6B-1 SQLite Snapshot History (2026-10-12)

### Baseline, scope and data flow

- Actual repository `/Users/zijing/projects/trippilot`, branch `main`; starting HEAD and fetched `origin/main` both `582a9100e143e2a2a963122d14a429c9ada39faf`. The original five Python cache contents are preserved. No reset, dependency addition/upgrade, model call, scheduler change, environment/key/proxy/CORS change or temporary project copy.
- Explicit save → synchronous `schedule.capture()` and current cost ref → immutable whitelist projection → frontend validation → `POST /saved-trips` → strict backend validation and pure scheduling replay → one SQLModel transaction → commit → success response. Preview creation/cancellation never substitutes a draft; adopting an adjustment changes the next captured official response.
- Version **`saved-trip-v1`** contains `requirements`, complete normalized `schedule` (including its own actual `request`, all edge references/traffic details, rules, unknowns, dates and unscheduled reasons), and raw user `costs`. No duplicate second schedule request. Requirement dates, daily window, full confirmed accommodation/must-visit snapshots and their order must agree with the schedule request.
- Only actual visit POI IDs are projected into ticket estimates; meals cover every trip date, including blank days; lodging/other remain whole-trip/all-traveler text. Empty string, explicit `0` and invalid text remain distinct. Preview-only new POI estimates are omitted until that POI is actually in the official timeline. Totals are not trusted or persisted: read-only display reuses `calculateScheduleCosts` and its integer-cent/unknown/invalid/range rules.
- No old Mock plan, active candidate pool/exclusion workflow, pending preview, natural-language intent, AI explanation, weather snapshot, map/bindings, selection, request/loading state or controller is archived. History is a separate read-only dialog; opening it leaves the working result mounted, and never generates a schedule or calls an external service.

### Contract and storage boundaries

- Routes: `POST /saved-trips`; `GET /saved-trips?offset=0&limit=10`; `GET /saved-trips/{id}`. Server-generated lowercase UUID hex IDs. List returns summaries only: name, dates, mode, status, server save time and schema version. Newest save first, ID tie-breaker; limit **1–20**, UI uses **10**, offset **0–100,000**. No overwrite/edit/delete/share/login or autosave.
- POST body ≤ **2 MiB**, 10-second body-receive deadline; reject duplicate JSON keys, nonfinite numbers, unknown/missing normalized fields and unsupported versions. General tree limits: depth 24, 200,000 nodes, 1,024 Unicode characters/string, 64 dictionary entries and 10,000 list entries; name 1–80 nonblank/control-free characters; each raw cost ≤256 characters. Existing 1–3 days, 6 required + 3 optional, bounded route/visit limits remain; archive travelers ≤1,000, interests/avoid lists ≤32, transit ≤60 legs and ≤256 geometry parts/leg. Oversize data fails explicitly, never truncates geometry. UTC generation/query times are required.
- Backend recursively checks exact Pydantic wire fields, strictly parses existing request/result models, reuses `ScheduleSnapshot` cross-reference/minute/mode validations and the existing pure `build_schedule` replay, then compares the **full normalized result**. Transit uses the existing traffic models plus archive bounds; no network verification. This demonstrates internal consistency of the submitted snapshot, **not authentication of the upstream data's origin**.
- Reading repeats version/content validation and verifies derived list metadata. Unsupported/corrupt content returns safe `invalid_record`, not silently filled defaults. Missing ID, operation conflict, too-large/invalid input and storage unavailable have controlled codes; database paths, SQL and underlying exceptions are not returned.
- Database: **`/Users/zijing/projects/trippilot/backend/data/saved-trips.sqlite3`**, resolved from backend source location, independent of startup CWD. The first archive access lazily creates the directory/empty schema; ordinary app startup/health does not touch the database. Later accesses/restarts use `create_all` only for missing tables, never drop/reset rows. This is **initial table creation, not a migration framework**.
- One table stores ID, unique operation ID, name, UTC saved time, version, derived list metadata and canonical validated JSON text. `app/saved_trips.py` owns engines, queries and transactions; request-level `Session` dependency closes/rolls back safely. SQLite busy wait is bounded at 3 seconds. Tests override the database-path dependency to a temporary disk database; they never use the user's database. Existing SQLModel dependency reused, following [official session-dependency guidance](https://sqlmodel.tiangolo.com/tutorial/fastapi/session-with-dependency/).
- Same operation ID + same canonical snapshot/name returns the existing row; differing content returns 409. The database unique constraint handles concurrent duplicates. Response is prepared before commit, but success returns only after commit. There is no automatic write retry. Browser timeout (15 seconds) or stopping waiting does **not** imply rollback; the explicit retry button resends the exact frozen operation ID/content, even if the current draft/costs changed. A new save click intentionally creates a new record.
- Save/read hooks isolate old success/error/cancellation using monotonic versions and AbortController. Old save success only acknowledges that named captured snapshot, never declares subsequently edited content saved. Rapid record switches clear prior content and reject late results. Save/history operations do not invoke Qwen, AMap, weather or route acquisition.
- `backend/data/`, SQLite databases and WAL/SHM/journal sidecars are ignored. Local archives may contain private travel details and stay outside Git/evidence. This is **local single-user storage with no account isolation**, not a public multi-user service. Future deployment needs authentication/ownership, backups/recovery, migrations and transaction/concurrency review; another DB also needs data migration and compatibility tests. Future contract changes must preserve/version old readers/replay semantics or explicitly migrate, not reinterpret historical JSON silently.

### Executed automatic checks

| Command | Actual result this round |
| --- | --- |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -q` | **318 passed**, existing Starlette/httpx warning only; no backend lint command configured |
| `cd frontend && npm test -- --reporter=dot` | **980 passed / 44 files** |
| `cd frontend && npm run lint` | Passed, also rerun after final read-only copy correction |
| `cd frontend && npm run typecheck` | Passed, also rerun after final copy correction |
| `cd frontend && npm run build -- --webpack` | Passed, also rebuilt final copy; existing Google Fonts network permission used |
| `cd frontend && npm test -- --reporter=dot src/components/trip/saved-history.integration.test.tsx src/components/trip/schedule-budget.test.tsx` | **5 passed** after final history wording correction; not represented as a second full-suite run |
| `git diff --check` and staged manifest/format/security checks | Required for stage-only closeout; no cache/env/DB/evidence files included |

- Disk tests exercise new initialization, repeated initialization/reopen persistence, summary pagination, walking/transit/required-prefix/blank-date/unscheduled cases, same-operation retries, 4-way concurrent duplicates, conflict, commit failure rollback, actual SQLite exclusive-lock error/recovery and corrupt JSON/unsupported versions. Unwritable storage/commit failure are controlled injected errors; not claims of actual production disk failure.
- Strict-boundary tests reject extra fields, bad references, altered replay/rules, inconsistent requirements, excessive text/body, duplicate JSON and nonfinite numbers. Twelve complete Python archive outputs are parsed by actual TypeScript runtime validation using the existing cross-language fixture pattern. No external HTTP is allowed in persistence tests.
- Hook/component tests cover frozen payload vs later changes, ambiguous timeout/retry without new operation ID, cancellation and old completion, out-of-order reads, unadopted vs adopted adjustment snapshots, related-cost projection, read-only rendering and no extra route/weather/candidate calls. Existing full-suite budget/adjustment/map/AI/state tests remained passing.
- Browser inspection caught a **copy-only** ambiguity from reusing the live budget panel's “refresh clears inputs” text. History now says the stored estimates are read-only and refresh does not clear/change the archive; invalid archived amounts cannot be edited here and are not converted to zero. This was rebuilt and the final copy rechecked in Chrome. No pricing/scheduling logic changed.

### Real Chrome acceptance and evidence

- Actual Chrome on the current Webpack **production** build. Verified project processes were reused/restarted explicitly; FastAPI used `python -B` with access logging disabled. All observations below are **2026-10-12 Asia/Shanghai** (UTC response dates are the preceding evening), not fabricated future data.
- Prepared one-day request 2026-10-12, 2 travelers, CNY3,000, confirmed accommodation **静安寺** and required **武康大楼**, photography candidates. Real walking official result and separately queried optional-exclusion preview both succeeded. Each retained one required plus one optional visit; remaining optional results kept original reasons (`partial`, not relabeled complete).
- Record **`f206276392794235bd13d7aece36900d`**, “6B-1 原正式草案”: saved at 02:28:10 while adjustment was **unadopted**, exactly equals original official schedule generated 02:25:04, not preview. Saved original optional 延中广场公园, omitted preview-only 辅德里公园 ticket estimate. Costs preserved ticket `0`, optional blank, meal `100`, lodging `500`, other `bad`; history correctly shows unknown/invalid, not ¥0 total.
- Record **`83430e1750184a4cab09bf9910350bb6`**, “6B-1 已采用调整草案”: saved at 02:28:31 after explicit adoption, exactly equals viewed preview generated 02:27:56; new optional 辅德里公园 ticket estimate `25.50` now included, old optional removed. With other `0`, subtotal **¥625.50**, budget difference **¥2,374.50**; these are user estimates, not real admission/lodging quotes. Changing current other estimate to `999` afterward did not alter either stored snapshot.
- Homepage empty history, newest-first two-record list, read-only requirements, route seconds/minutes/query times, lunch/wait, 14:28 return and unarranged reasons were inspected. After a full page refresh the second record was unchanged. After safely restarting FastAPI on the same DB, **both** records returned 200 with identical canonical full-snapshot SHA256, original save/generation/query times and cost text. No database or raw response was exported as evidence.
- Live read-only dialog at **400×900** had client/scroll width **374/374**; details and budget summary remained usable. Existing map regression used real 静安寺/武康大楼 bindings, real **2.69 km / estimated 36 min** walking line, activity→centered highlighted Marker, fit-full and Marker→selected activity with prior Marker reset. It did not change archives or formal schedule. Initial off-screen Marker click was retried only after fitting the full route, not forced through another element.
- Observed local API counts across preparation/regression: POI search **4**, Mock `/trips/plan` **1**, candidates **1**, schedule-preview **2**, weather **1**, standalone walking **1**. Archive actions: POST `/saved-trips` **2**, list GET **6**, detail GET **5**. Each isolated save only added its own archive POST; after full reload all **6** backend requests were archive GETs. **0 new external business requests from archive actions; 0 Qwen requests this round**. These browser counts are not a claim about upstream retry/HTTP-attempt counts. SDK/static traffic is excluded from these business counts.
- Stable external evidence: [sanitized receipt](/Users/zijing/projects/trippilot-evidence/6b-1/receipt.json). **All 13 screenshots were opened and visually inspected**, including final-copy replacement of screenshot 05:
  - [01 empty homepage history](/Users/zijing/projects/trippilot-evidence/6b-1/01-empty-history-home.png)
  - [02 save original before adoption](/Users/zijing/projects/trippilot-evidence/6b-1/02-save-original-before-adoption.png)
  - [03 two-record list](/Users/zijing/projects/trippilot-evidence/6b-1/03-history-list.png)
  - [04 original read-only requirements](/Users/zijing/projects/trippilot-evidence/6b-1/04-original-history-detail.png)
  - [05 unknown, zero and invalid cost inputs](/Users/zijing/projects/trippilot-evidence/6b-1/05-original-unknown-zero-invalid-costs.png)
  - [06 400px history](/Users/zijing/projects/trippilot-evidence/6b-1/06-history-mobile-400px.png)
  - [07 adopted cost summary, 400px](/Users/zijing/projects/trippilot-evidence/6b-1/07-adopted-budget-mobile.png)
  - [08 original route estimates and visits](/Users/zijing/projects/trippilot-evidence/6b-1/08-adopted-timeline.png)
  - [09 activity→Marker](/Users/zijing/projects/trippilot-evidence/6b-1/09-map-activity-to-marker.png)
  - [10 Marker→activity](/Users/zijing/projects/trippilot-evidence/6b-1/10-map-marker-to-activity.png)
  - [11 history after page refresh](/Users/zijing/projects/trippilot-evidence/6b-1/11-reloaded-history.png)
  - [12 same archive after backend restart](/Users/zijing/projects/trippilot-evidence/6b-1/12-after-backend-restart.png)
  - [13 lunch, return and unarranged reasons](/Users/zijing/projects/trippilot-evidence/6b-1/13-return-and-unarranged-reasons.png)
- Live archives here are **walking**, not transit. Transit archive/unknown fare, unarranged-required/empty days, concurrency, response loss, malformed records and faults are controlled disk/contract/state tests, not real upstream or real device failure acceptance. Physical phone, dedicated subway/transfer samples, maximum six-required/three-optional live load, historical transit `data_error`, batch-local throttling and deadline-vs-cleanup restrictions remain unchanged. The existing Turbopack/network/dependency-advisory limitations still apply. History does not certify present traffic, weather, prices, opening hours or reservations.

### Delivery manifest (20 files)

1. `.gitignore`
2. `backend/main.py`
3. `backend/app/api/saved_trips.py`
4. `backend/app/saved_trips.py`
5. `backend/app/schemas/saved_trip.py`
6. `backend/tests/export_saved_contract.py`
7. `backend/tests/test_saved_trips.py`
8. `frontend/src/app/page.tsx`
9. `frontend/src/components/trip-plan-result.tsx`
10. `frontend/src/components/trip/cost-estimate-input.tsx`
11. `frontend/src/components/trip/schedule-budget.tsx`
12. `frontend/src/components/trip/save-trip.tsx`
13. `frontend/src/components/trip/saved-history.tsx`
14. `frontend/src/components/trip/saved-history.integration.test.tsx`
15. `frontend/src/lib/api.ts`
16. `frontend/src/lib/saved-trips.ts`
17. `frontend/src/lib/saved-trips.test.ts`
18. `frontend/src/lib/use-saved-trips.ts`
19. `frontend/src/lib/use-saved-trips.test.tsx`
20. `docs/PROGRESS.md`

Only these files belong to 6B-1. Existing five Python caches, private database/sidecars, environment files, build artifacts and the external evidence directory are excluded. Normal commit/push after final checks is authorized; stop at 6B-1, no history-edit workflow.

## Milestone 6A-2 Natural-language Adjustment Preview (2026-10-12)

### Baseline, scope and contract

- Actual project, not a copy: `/Users/zijing/projects/trippilot`, `main`, baseline `c23ad4055131756248ca5092b2d4c52f7d84d280`. The user-confirmed 6A-1 commit is pushed. Its 300 backend / 931 frontend tests and 18 model calls are historical baseline records; the new executions are listed below. No dependency, environment, key, proxy, CORS, scheduler or budget-rule change
- One input can propose **excluding one actual scheduled optional POI only**. Required, unarranged, unknown and cross-draft IDs cannot execute. No multi-action partial execution, manual replacement, automatic indoor choice, chat, provider switch or model-written schedule. The original candidate exclusion remains an immediate action; natural language instead creates an isolated preview
- Explicit click → `explanationSnapshot` → `POST /trips/adjustment-intent` → existing snapshot replay/validation → one Qwen classification → strict target/scope check → existing `/trips/schedule-preview` → existing comparison → explicit adoption. The intent endpoint itself does not request AMap or mutate state
- New `adjustment-intent-v1` request carries program-managed request ID/draft version, a 1–400-character instruction and the existing bounded compact schedule snapshot. Unknown fields, malformed/duplicate-key JSON, invalid replay/references and excessive bodies fail before model invocation. Reuse the 64 KiB/body deadline and deterministic scheduler replay; internal consistency is not authentication of a client snapshot's provenance
- The model receives only the instruction and actual visit entities with ID/name/required-or-optional/date. It does not receive coordinates, full geometry, route prose, weather, cost, history or formal versions. Server-built strict JSON Schema has `execute`, `clarify`, `unsupported` branches; executable target enum contains only scheduled optional IDs. Output is a whitelist `{decision, action, target_id, reason_code}`, not free factual prose. The program renders all user messages
- The model is not sole execution authority: after ID/role validation, a conservative whole-instruction gate requires an explicit single exclusion clause grounded to the exact unique name or ID, optionally preserving required/other inputs. Negation, ambiguous names and complex phrasing are clarified rather than guessed; recognized additional modifications are unsupported. This deliberately narrows language coverage. A same-name place needs its ID; names and injection strings remain plain data. It does not run a second model, choose a default target or retry a rejected result
- Reused backend-only Beijing configuration/SecretStr from 6A-1 unchanged; no key re-entry. Shared `QwenClient.generate` keeps official fixed endpoints, no redirects, nonthinking/nonstreaming JSON Schema, at most 1,024 output tokens, bounded bodies and a 20-second whole-operation deadline. Exactly one model attempt per click, **no retries or repair**. Frontend parse wait is 25 seconds; a subsequent separately authorized preview route request retains its existing 25-second wait and route-query limits. The full two-step action can therefore take longer than one timeout. Disconnect cancels local work; upstream billing cessation is not guaranteed

### State, comparison and adoption

- Extended the existing adjustment hook, not a second preview/commit implementation. It owns one active source (`weather` or `natural`), parser controller, route controller and monotonic identity. Editing the instruction, cancel, new request or official draft invalidation aborts work; old success/error/finally and old cancel/adopt callbacks cannot publish or commit. Current draft capture reads existing synchronous refs, including mode ABA protection
- A natural-language action freezes the current official response/settings, candidate transaction snapshot and target. It uses the same `prepareExclusion` / `prepareAdjustmentInput`, first-three selection, retained/default stays, strict response validation, before/after timelines and shared cost comparison as 5C-3/5D-2. Preview loading/failure/cancel does not alter official exclusions, settings, response or existing explanation
- Both entry points share `adoptionIssue` and `schedule.adoptAdjustment`: event-time official version/candidate identity/required-order/actual-visit/return checks precede one synchronous candidate-settings-response commit. Valid partials remain eligible when all required visits are arranged. Double/old adoption cannot commit twice. No extra request on adoption; candidate exclusion, selected inputs and official draft update consistently, and existing explanation/weather report invalidate
- Natural adjustments are an explicit user choice, **not a weather conclusion**, so they do not require or subscribe to a weather-check report. Weather refresh/annotations and fee edits do not expire them. Weather-origin previews retain their original report/time guards. Both sources expire on official inputs/candidate changes/regeneration/adoption/edit/unmount. Old Mock date/map/Marker operations stay independent. The source distinction avoids accidentally retaining an expired weather-derived proposal or clearing a manual one on unrelated weather activity
- Comparison uses existing actual ID/date/time/return and cost rules. Unknown costs remain unknown; estimates do not become verified prices, a replacement does not become an indoor recommendation, and different route batches are not claimed identical or optimal

### Delivery manifest — 17 files

1. `backend/app/api/explanation.py` — extract/reuse disconnect cleanup and bounded route/client
2. `backend/app/integrations/qwen.py` — shared single-attempt transport/content validation, preserve explanation parser
3. `backend/main.py` — register the independent intent route
4. `backend/app/api/adjustment_intent.py` — strict snapshot + intent API, safe errors
5. `backend/app/schemas/adjustment_intent.py` — bounded request, decision and echo contracts
6. `backend/app/services/adjustment_intent.py` — actual entities, constrained prompt/schema and independent authorization
7. `backend/tests/test_adjustment_intent.py` — scope/security/replay/deadline/cancellation tests
8. `backend/tests/export_intent_contract.py` — offline Python→TypeScript intent fixtures
9. `frontend/src/lib/adjustment-intent-api.ts` — timed request, strict runtime guard and code-owned messages
10. `frontend/src/lib/adjustment-intent-api.test.ts` — cross-language/API validation and no-retry tests
11. `frontend/src/lib/use-schedule-adjustment.ts` — shared source-aware parse/preview/adopt lifecycle
12. `frontend/src/lib/use-natural-adjustment.test.tsx` — state/ABA/cancel/adopt/independence coverage
13. `frontend/src/components/trip/natural-adjustment.tsx` — explicit input, current target identities and clarify/failure UI
14. `frontend/src/components/trip/natural-adjustment.integration.test.tsx` — actual result composition with controlled transport
15. `frontend/src/components/trip/schedule-adjustment.tsx` — reuse existing comparison for this entry point
16. `frontend/src/components/trip-plan-result.tsx` — mount alongside formal draft and explanation
17. `docs/PROGRESS.md` — this record; historical evidence/limitations preserved

### Automated checks actually executed this round

| Command | Result |
| --- | --- |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -q` | **309 passed**, all 300 baseline tests retained; existing Starlette/httpx deprecation warning |
| `cd frontend && npm test -- --reporter=dot` | **963 passed / 41 files**, including the 931 baseline tests |
| `cd frontend && npm run lint` | Passed |
| `cd frontend && npm run typecheck` | Passed |
| `cd frontend && npm run build -- --webpack` | Passed; production frontend restarted from this build |
| `git diff --check` / staged whitespace check | Passed in final delivery review |
| Original five cache content hashes | Unchanged; excluded from stage |

Controlled tests cover explicit optional exclusion; retained required order; same names/different IDs; negation/ambiguity/multiple changes/out-of-scope and prompt injection; strict fields/invalid IDs; replay inconsistency; parser/route timeout/failure/cancel; one model attempt and disconnect cleanup; late success/error/finally and reverse completion; mode/candidate ABA; stale cancel/adopt; no-visits and incomplete-required rejection; fourth candidate/default stay; subsequent ordinary generation using adopted input; existing AI retained on cancel and cleared on adoption; cost/weather/map independence. Three Python decision branches pass the real TypeScript validator, with adversarial response mutations rejected. These use controlled transports, not real provider faults. Test-only harness fixes corrected a date fixture and React event ordering; no production validation was relaxed for a passing test

### Real Chrome acceptance — two remaining Qwen attempts, no more

- Restarted only the verified existing TripPilot frontend/backend processes to serve this version. Backend uses `python -B` and no access log; actual production frontend at `localhost:3000`, backend at `127.0.0.1:8000`. No environment values, auth headers, raw model output or sensitive SDK URLs were printed/saved
- Actual form: 2026-10-12, Shanghai, two travelers, accommodation 静安寺, required 武康大楼, photography interest. Real candidate query and real walking draft arranged the required visit plus 延中广场公园. No fixture/interception/Mock data was used for these inputs, route results or model decisions
- **Cumulative call 19** (stage 1): fixed `qwen3.7-flash-2026-07-15`, `adjustment-intent-v1`, execute/single_optional, 200, about **1,030 ms**, usage **288 input / 59 output tokens**. Exact user request excluded 延中广场公园 and preserved required visits. One intent request led to exactly one schedule preview. Before adoption, official draft and candidate-panel text were unchanged
- Actual preview removed 延中广场公园 and added 辅德里公园. Required 武康大楼 stayed 09:36–10:36; optional 13:00–14:00, lunch 12:00–13:00, home return changed from 14:44 to 14:28. Existing comparison disclosed route batches and unknown fees. Editing the shared user estimate to 20 yuan and querying real weather did not expire the manual preview or change the original schedule
- Adopted the viewed preview: **zero additional business requests**; official generation time, visits, timeline and 14:28 return matched the viewed response. Candidate panel showed the target excluded; fourth candidate entered the prepared first-three input. Required visit remained. Preview was consumed, not re-requested
- **Cumulative call 20** (stage 2): same model/contract, clarify/ambiguous_target for “轻松一点”, 200, about **2,774 ms**, **278 input / 33 output tokens**. No extra schedule request, no executable proposal, formal draft unchanged. Both live responses passed business checks, not just HTTP status. Total is now **20 / 20**, including the earlier 18; zero stage retries and no further model calls
- Chrome 400px viewport: input, target IDs, clarify/preview controls and full comparison use a single column; measured no horizontal document overflow. This is not physical-phone/touch acceptance
- Existing real map regression: bound 静安寺 and 武康大楼 to two adjacent Mock activities, queried one actual walking route (200). Visible basemap/roads/names/copyright and route; activity→Marker centered/highlighted the selected point, Marker→activity selected the second card, complete-route view retained the line and points. Official adopted draft and natural intent state were unchanged
- Safe local business counts: `/trips/plan` 1, `/places/search` 4, `/places/candidates` 1, `/trips/schedule-preview` 2, `/trips/adjustment-intent` 2, `/weather/forecast` 1, `/routes/walking` 1; `/trips/schedule-explanation` and `/routes/transit` 0. All twelve observed responses were 200. These are **local browser business counts**, not an observation of every AMap upstream attempt; Qwen's two calls are also bounded by the no-retry adapter and corresponding usage receipts

### Stable evidence, verification limits and handoff

Evidence directory: [/Users/zijing/projects/trippilot-evidence/6a-2/](/Users/zijing/projects/trippilot-evidence/6a-2/), outside Git. Every screenshot below was saved and opened individually; the long comparison/draft images are full-section captures, with the candidate screenshot focused on the visible scroll area containing the affected target

| Evidence | What was actually observed |
| --- | --- |
| [01-before-explicit-request.png](/Users/zijing/projects/trippilot-evidence/6a-2/01-before-explicit-request.png) | Explicit instruction and eligible original target; no input-triggered call |
| [02-real-preview-desktop.png](/Users/zijing/projects/trippilot-evidence/6a-2/02-real-preview-desktop.png) | Real new/old places, required timing, return and unknown-cost comparison |
| [03-natural-400px.png](/Users/zijing/projects/trippilot-evidence/6a-2/03-natural-400px.png) | Natural input and ready state at 400px |
| [04-preview-400px.png](/Users/zijing/projects/trippilot-evidence/6a-2/04-preview-400px.png) | Full narrow comparison, shared estimate and adopt/cancel controls |
| [05-adopted-formal.png](/Users/zijing/projects/trippilot-evidence/6a-2/05-adopted-formal.png) | Adopted formal timeline, required visit, lunch and actual return |
| [06-adopted-candidates.png](/Users/zijing/projects/trippilot-evidence/6a-2/06-adopted-candidates.png) | Original target excluded only after adoption |
| [07-real-clarification.png](/Users/zijing/projects/trippilot-evidence/6a-2/07-real-clarification.png) | Second actual Qwen decision rendered as program-owned clarification |
| [08-activity-marker-real-route.png](/Users/zijing/projects/trippilot-evidence/6a-2/08-activity-marker-real-route.png) | Activity selects/centers highlighted real Marker while route remains |
| [09-marker-route-fit.png](/Users/zijing/projects/trippilot-evidence/6a-2/09-marker-route-fit.png) | Marker-selected second activity and complete real walking route view |
| [acceptance.json](/Users/zijing/projects/trippilot-evidence/6a-2/acceptance.json) | Minimal model/contract/usage/duration/count receipts and observed assertions; no prompts/raw outputs/keys/full itinerary logs |

- **Controlled only this round:** natural-preview cancellation preserving an already populated AI explanation, adoption clearing that explanation, live-provider error/refusal/timeout/injection, adverse timing/ABA and transit-mode natural adjustment. They passed offline actual-hook/component tests. Budget was deliberately reserved for the two requested normal/clarify samples, so there was no third Qwen call to repeat cancellation/retry or recreate an explanation. Historical 6A-1 live cancellation/AI records are retained, not labeled new live 6A-2 verification
- No claim of general natural-language coverage, real rainy-weather decision, physical phone, dedicated subway/transfer operation, full-load route acceptance or real provider fault reproduction. Existing AMap transit `data_error`, request-local throttling versus account-wide limits, search-cutoff/cleanup distinction, unknown costs/opening/booking, dependency advisories and Turbopack restrictions still apply
- Interview summary: the model selects a tightly scoped intent, not a new itinerary. Program code checks both current entity/constraint identity and the explicit request, then computes a read-only preview using existing real-route planning. Only a fresh, explicit adoption atomically publishes candidate exclusion/settings/formal response. Monotonic identities reject ABA and late responses; source-specific invalidation separates user intent from weather-derived advice. No new Agent system, optimizer or parallel commit path

## Milestone 6A-1 Implementation and Offline Checks (2026-10-11)

### Baseline and initial offline gate (historical, before local configuration)

- Actual project `/Users/zijing/projects/trippilot`, branch `main`, HEAD `796280180ffd6b104d3ff5840ee68d1cb8f71028`. Existing remote tracking record is equal (0 ahead / 0 behind); no fetch, reset, commit or push during this implementation phase. No temporary project copy, dependency, Agent framework or multi-provider layer
- `.env` / `.env.local` were not read, printed or modified. Only the placeholder `.env.example` was extended. All five pre-existing Python cache hashes still match the initial record; backend checks use `python -B`. These caches, environment files and external evidence must never be staged
- **Waiting for local configuration**, not fully accepted. Backend keys are not needed for automated checks. Real Qwen calls so far: **0**, including no controlled-sample live calls. The maximum for this entire acceptance round remains **20**, not a per-script allowance
- Configuration instructions: [QWEN_SETUP.md](QWEN_SETUP.md). User edits `/Users/zijing/projects/trippilot/backend/.env`: `QWEN_API_KEY`, `QWEN_BASE_URL`, `QWEN_MODEL`. Default snapshot is `qwen3.7-flash-2026-07-15`; confirm the console's Beijing-compatible URL before live calls. Do not ask the user to send a key. Start live acceptance only after “本机配置已填写”

### Contract, data flow and deliberate boundaries

1. `use-schedule-preview.ts::capture()` now reads the current official response and version from synchronous refs, not an old rendered response combined with a new version. Every state publication updates its ref first. Ordinary generation/settings/candidate changes and successful adoption publish through an official-draft-only invalidation channel. There is no subscription from AI explanation to the shared weather-check revision
2. Explicit `解释当前行程` → existing `isScheduleResponse` → a whitelisted compact snapshot → `POST /trips/schedule-explanation`. Snapshot includes request POI identity/name/coordinates, stays/lunch/mode, dates/items, bounded normalized edge outcomes, unscheduled codes and optional attempts. Geometry, transit leg prose, fees, weather, arbitrary explanations and history are excluded. Coordinates remain backend-only validation inputs and are never included in the model fact catalogue
3. Independent strict Pydantic schema, no extra fields, bounded strings/lists, 64 KiB request limit applied before JSON parsing (including chunked requests), duplicate-key/nonfinite JSON rejection, exact 1–3-day / 6-required / 3-optional constraints and at most 29 directed outcomes. Reuse `required_edges`, `optional_edges` and the **existing pure scheduler** to replay the submitted normalized movement estimates. Compare its full compact output: identity/coordinates/mode, edge IDs/used flags, date/order/time windows, seconds→ceil-minutes, lunch/waits, home returns, required prefix and optional attempts/reasons must match
4. The only change to `services/schedule.py` is an optional result-envelope type parameter, defaulting to the existing response. Explanation replay uses a validated compact edge envelope without inventing fares, distances or transit geometry; scheduling rules and the original `/trips/schedule-preview` response are unchanged. Internal consistency is **not authentication of client-supplied AMap provenance**, and the UI says so
5. Backend constructs typed `f0…` facts from validated data. Qwen selects/orders at most 6 `{message_code, fact_ids}` highlights with at most 3 references each. Strict JSON Schema bounds the shape and restricts IDs to the actual catalogue. Post-validation rejects unknown, repeated and incompatible-type references; program code renders the factual sentences. The frontend also verifies the returned request identity, schedule time, metadata and reference types against its compact catalogue layout. External POI names remain data and are rendered as plain React text
6. Mandatory missing-required, reference-estimate and opening/reservation limitations are generated by code, not optional model text. Valid partial or fully unscheduled drafts can be explained without describing them as complete; no optimality, source re-verification or timetable guarantee. No weather/cost explanation and no scheduling, budget, candidate, map, proxy, CORS or adoption-rule change
7. Only Beijing official HTTPS hosts and fixed `/compatible-mode/v1` are accepted server-side; no credentials in URL, alternate port/path/query/fragment, redirection or frontend-selected upstream. `QWEN_API_KEY` is `SecretStr`. The fixed snapshot is default; the only alternate permitted server setting is the same-family `qwen3.7-flash`, never selected automatically. Missing/unsupported config gives a safe explanation-only 503; existing services can start normally
8. One explicit action → **one** nonthinking/nonstreaming model POST, strict JSON Schema, **1,024 output Token** maximum, no tools/search/repair/retry. Whole operation deadline **20 seconds**, frontend **25 seconds**, bounded 32 KiB upstream body / 48 KB fact prompt / 8 KiB model content. Cleanup can add time; browser cancellation triggers backend cancellation but cannot promise stopped provider billing. Authentication/quota/rate limit/model availability/timeout/refusal/truncation/empty/structure/reference errors are controlled and never expose raw provider messages. Low-level HTTP/auth details are redacted
9. Explanation hook owns its AbortController/request identity and monotonic version; success/error/finally and old cancel callbacks are guarded. Official draft invalidation synchronously cancels/clears; fees, weather refresh/annotations, Mock date/map/Marker operations and unadopted adjustment start/success/failure/cancel/expiry do not. A fresh successful adoption clears explanation without issuing a model request. Rerendering does not resubscribe or cancel via unstable composed callbacks

### Delivery manifest — 22 files

1. `backend/.env.example` — blank key/URL and nonsensitive default model
2. `backend/app/config.py` — optional server-only Qwen settings
3. `backend/app/logging_filters.py` — suppress bearer/workspace HTTP details
4. `backend/app/schemas/explanation.py` — bounded snapshot and whitelist output contracts
5. `backend/app/services/explanation.py` — existing-rule replay, typed facts and program rendering
6. `backend/app/services/schedule.py` — alternate internal result envelope only; unchanged rules
7. `backend/app/integrations/qwen.py` — one-attempt bounded Beijing adapter and safe errors
8. `backend/app/api/explanation.py` — bounded independent endpoint and disconnect cleanup
9. `backend/main.py` — register explanation router
10. `backend/tests/explanation_samples.py` — twelve controlled business samples
11. `backend/tests/export_explanation_contract.py` — offline Python→TypeScript contract fixtures
12. `backend/tests/test_explanation.py` — snapshot/fact/prompt/error/security/deadline tests
13. `frontend/src/lib/use-schedule-preview.ts` — current capture and official-only invalidation subscription
14. `frontend/src/lib/explanation-api.ts` — whitelist projection, response guard and timed request
15. `frontend/src/lib/explanation-api.test.ts` — projection, requests and twelve cross-language cases
16. `frontend/src/lib/use-schedule-explanation.ts` — independent explanation lifecycle
17. `frontend/src/lib/use-schedule-explanation.test.tsx` — ABA/cancellation/old callback and independence tests
18. `frontend/src/components/trip/schedule-explanation.tsx` — explicit AI panel and fixed limitations
19. `frontend/src/components/trip/schedule-explanation.integration.test.tsx` — actual result composition, fees/weather/adjustment/maps
20. `frontend/src/components/trip-plan-result.tsx` — mount alongside official draft
21. `docs/QWEN_SETUP.md` — configuration, official references and acceptance limits
22. `docs/PROGRESS.md` — actual progress, checks and remaining gate

### Automated checks actually executed

| Command | Result |
| --- | --- |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -q` | Passed: **299 tests**; existing Starlette/httpx deprecation warning retained |
| `cd frontend && npm test` | Final full run passed: **931 tests / 38 files** |
| `cd frontend && npm run lint` | Passed |
| `cd frontend && npm run typecheck` | Passed |
| `cd frontend && npm run build -- --webpack` | Passed; frontend restarted using this build on port 3000 |
| `git diff --check` | Passed |
| Original five cache SHA-256 comparison | All unchanged |

The first frontend full run had one existing `place-explorer` timing assertion (candidate DOM appeared before Marker effect); the focused file and the subsequent whole suite passed. No map production code or existing map test was modified to hide it. Backend tests exposed a cancellation-watcher issue during implementation; replaced polling `is_disconnected` with a cancellable ASGI disconnect wait, and added an explicit disconnect/one-call/cleanup test. The two hung diagnostic processes were stopped, not the user's backend

Controlled samples: complete walking; required prefix with missing required; no visits due to timeout; optional failure; lunch/wait; three dates with blank days; transit with unknown legs/fare/geometry; unsupported transit; optional-only; same-place zero movement; injection text/same-name-different-ID; cross-year user stay. Every sample replays and every catalogue fact/type is exercised. Python output/projection and the actual TypeScript guard agree for all twelve. These are **offline fixtures**, not real-model usefulness evaluation or real AMap failure acceptance

### Initial real-acceptance gate (historical; subsequently completed below)

- Chrome control is available and its existing TripPilot tab was detected. This round has **not yet executed real model/browser business acceptance**, nor captured an AI success screenshot. Model account access, actual strict-schema support, highlight usefulness/non-repetition, live cancellation/adoption/independence and 400px acceptance remain pending
- Stable directory prepared: [/Users/zijing/projects/trippilot-evidence/6a-1/](/Users/zijing/projects/trippilot-evidence/6a-1/). No screenshot or raw network/model log has been saved yet. After configuration, save and individually open each screenshot; record only minimal sanitized model/contract/duration/result/token/call-count metadata. Label live model calls over controlled schedules **真实千问＋受控行程样本**, separately from real itinerary/browser acceptance
- Existing backend service on `127.0.0.1:8000` was not stopped or restarted yet. It still serves the preceding implementation until the explicitly planned configuration-time restart. Frontend production startup succeeded after this build; starting UI is not claimed as real AI acceptance
- Historical transit `data_error`, physical-phone/touch, dedicated real subway/transfer samples, broader upstream faults, six-required-plus-three-optional full load, request-local pacing versus account limits, route search-cutoff/cleanup distinction and Turbopack/dependency limits remain. No old failure or controlled sample is relabeled as a new real success
- **No commit/push until remaining live acceptance passes.** Stop at 6A-1. The interview explanation is: deterministic code owns the facts and constraints; a single model chooses useful highlights; strict references prevent fabricated claims, and official-draft identity—not weather or render identity—controls explanation freshness

### Configured real acceptance and final checks (2026-10-11–12, Asia/Shanghai)

- User confirmed local configuration. Read it through `Settings` and checked only presence plus official Beijing endpoint/model validity; no environment contents, credentials or workspace URL were printed or edited. Restarted the verified backend on 8000 with `python -B`; reused the current frontend on 3000. The fixed `qwen3.7-flash-2026-07-15` was actually used, not an alias/provider fallback
- **18 total upstream model attempts**, each from one explicit acceptance action, with no automatic retry or repair. Calls 2–11 passed ten controlled business samples. Call 1 returned 200, but the acceptance harness mistakenly used strict Python rather than JSON datetime validation; duration/usage were not retained, so it is not counted as a completed acceptance. Calls 12 (injection sample) and 13 (first real-browser explanation) returned controlled `references` failures. Original drafts remained intact. No raw model response was retained, so these records do not claim which individual reference was wrong
- Investigated the generation contract: its original all-ID enum did not constrain the relationship between each message code and its allowed fact IDs. Tightened, **not relaxed**, the provider schema using code-specific `anyOf` branches and added the allowed code on each input fact. Clarified global non-repetition in the prompt; backend duplicate/type checks remain mandatory. Official structured-output documentation supports `anyOf` but not `uniqueItems`; no unsupported uniqueness keyword, repeated call loop or fabricated fallback was added. Added a regression test over all twelve catalogues
- After that change, call 14 passed actual walking-draft browser acceptance; calls 15–16 passed the injection and cross-year controlled samples; calls 17–18 passed adopted-walking and transit-draft browser acceptance. Thus all twelve prepared controlled samples have an observed valid real-Qwen selection, across the pre/post-hardening runs; they were **not all rerun after hardening**. The final version has five independently validated sample/browser contexts. Final automated regression covers every catalogue. This finite sample is not a guarantee that every future response will validate
- Real browser inputs used confirmed Shanghai POIs and actual AMap routes, not controlled fixtures: accommodation reference 静安寺, required 武康大楼, real optional park candidates. Walking and transit official drafts each arranged the required and one optional visit. Displayed code-owned facts matched current visits, lunch/wait and home return; mandatory estimate/opening/reservation limitations remained visible. Different route batches were not claimed identical or optimal. The model decides highlights; it does not necessarily select every visit fact
- Weather lookup and refresh both returned real responses for the requested day. This was **no triggered rain/snow rule + user-directed adjustment**, not live rainy-weather acceptance. Real adjustment replaced an optional park, cancel preserved the original explanation, and adopt cleared it without a model call. Newly adopted state was explicitly explained once, then ordinary regeneration cleared that explanation immediately and did not automatically regenerate it

| Real Chrome check | Actual observation | Opened evidence |
| --- | --- | --- |
| Initial guarded failure | Reference error displayed, official draft unaffected; not counted as AI success | [00 rejected reference](/Users/zijing/projects/trippilot-evidence/6a-1/00-rejected-reference.png) |
| Walking explanation | Explicit click, real fixed Qwen, backend reference checks and frontend contract/rendering passed | [01 real Qwen](/Users/zijing/projects/trippilot-evidence/6a-1/01-real-qwen-explanation.png) |
| Unadopted preview / cancel | Loading and ready preserved AI text; cancel preserved original text and model count | [02 preview](/Users/zijing/projects/trippilot-evidence/6a-1/02-adjustment-preview.png), [03 cancel](/Users/zijing/projects/trippilot-evidence/6a-1/03-cancel-keeps-explanation.png) |
| Map/Marker regression | Real tiles, two bound POIs, actual walking polyline; activity focus and Marker→activity selection worked; AI unchanged | [04 map walking](/Users/zijing/projects/trippilot-evidence/6a-1/04-map-markers-walking.png) |
| Narrow viewport | 400px, no page overflow; full AI panel and limitations inspected | [05 mobile](/Users/zijing/projects/trippilot-evidence/6a-1/05-mobile-explanation.png) |
| Adoption | Previously displayed preview adopted; old AI cleared, no model request | [06 adoption](/Users/zijing/projects/trippilot-evidence/6a-1/06-adoption-clears-explanation.png) |
| Ordinary regeneration | Explanation cleared at generation start and stayed cleared on success; explicit next click required | [07 regeneration](/Users/zijing/projects/trippilot-evidence/6a-1/07-regeneration-clears-explanation.png) |
| Transit explanation | Real transit draft with required + optional visits; selected facts and fixed transit timetable caveat displayed | [08 transit explanation](/Users/zijing/projects/trippilot-evidence/6a-1/08-real-transit-explanation.png) |
| Independent old map | Explicit real transit route showed segmented geometry; map mode and Mock day switches did not clear AI | [09 map transit](/Users/zijing/projects/trippilot-evidence/6a-1/09-old-map-transit.png) |

Fee entry, weather query/refresh and exposure annotation also preserved the exact AI text, with no extra model call. Browser-local request observations: POI search 4; Mock plan 1; candidate batch 1; schedule preview 5 (including two explicit adjustments); explanation 4; weather 2; standalone walking 1; standalone transit 1. These local counts are **not** a count of all upstream AMap attempts. Model attempts were separately bounded and counted across controlled scripts and browser: 18. Safe receipt: [acceptance.md](/Users/zijing/projects/trippilot-evidence/6a-1/acceptance.md). Screenshots contain page UI only; no HAR, headers, prompt, raw model response or complete itinerary log was saved

Final commands after schema hardening: `cd backend && .venv/bin/python -B -m unittest discover -s tests -q` **300 passed**; `cd frontend && npm test -- --reporter=dot` **931 / 38 files passed**; `npm run lint`, `npm run typecheck`, `npm run build -- --webpack` **passed**. Five cache SHA-256 hashes still match the initial record. Earlier 299-test/offline results above are retained as history, not substituted for this final run

Closeout check (2026-10-12): `git fetch origin main` confirms the baseline above equals `origin/main` (0 ahead / 0 behind before this delivery); no divergence or unpushed historical bundle. Only the listed 22 files are in scope. Configured-secret-value scan and new-file whitespace checks pass. Stable external evidence, both actual environment files and all five Python caches are excluded. Ordinary commit/push only; no reset/amend/force push or next-stage work

Remaining limits: live failure/timeout/cancel races and old success/error/finally/ABA branches are primarily controlled automated tests, not induced provider faults. The two observed reference failures were safely rejected; hardening improves constraints but cannot promise every model call succeeds. Cancel cannot guarantee upstream billing stops. No dedicated live subway/transfer-service guarantee, physical-device acceptance or full-load live route test was added. Historical `data_error`, 20-second route cutoff versus cleanup, request-local throttling, Turbopack and dependency limitations remain. AI does not interpret weather/budget or alter any schedule/adoption rule. Interview summary: code owns facts, one model selects priorities, a strict typed-reference gate controls rendering, and an official-draft revision—not unrelated weather/UI state—controls freshness

## Milestone 5D-2 Actual Implementation and Acceptance (2026-10-10)

### Baseline, scope and design

- Actual project `/Users/zijing/projects/trippilot`, branch `main`, starting HEAD `04cac8500925cc62a87b1d02c8cfd14563bf6e64` (accepted/pushed 5D-1). Final `git fetch origin main` confirmed that same remote HEAD, zero ahead/behind before this commit and no divergence. Initial worktree contained only the five original Python cache changes; their content hashes remain unchanged. No temporary delivery copy, reset, unrelated cleanup or secret/environment changes
- Reuse the existing 5C-3 adjustment preview and 5D-1 calculation. No backend, API, scheduling rule, route budget, weather/POI request behavior, dependency, persistence or map change. Existing backend service reused; frontend restarted on its existing port with the successfully checked Webpack production build
- `TripPlanResult` now owns the **single** `CostInputs` state. `ScheduleBudget` and adjustment comparison receive that same object and edit callback. Ticket estimates remain POI-ID scoped, meals date scoped, lodging/other whole-trip all-traveler totals. Only current values are stored, not cost results, another formal schedule, request snapshots for costs, or new budget revisions
- `compareScheduleCosts` calls existing `calculateScheduleCosts` twice: the preview's frozen `input.base.original` and its actual response, both with the same captured trip budget/travelers and current fee inputs. Actual movement occurrences, integer cents, null fares, unknowns, exact conversion and overflow rules remain in the original calculator. Neither all queried edges nor cached-edge uniqueness determines charging
- Signed difference is **new subtotal minus original subtotal**, computed with `BigInt` and checked against safe integer-cent limits. It appears only when both sides validate, have visits, have all required visits arranged and have no invalid/unknown costs. Same missing labels never offset. A valid partial containing all required visits can compare; unused optional trials do not add missing costs. Each side retains known subtotals, missing items and qualified budget difference; invalid money removes that side's totals and any old overall difference immediately
- `ScheduleAdjustment` mounts comparison/editor only for its existing ready/adoptable itinerary. Loading, failure, expiry, empty or required-incomplete output has no active cost comparison/editor; historical timelines remain inspectable. New `canUsePreview` reuses current state/session identity, prepared candidate token and action-time weather authorization for preview-only edits, including old handlers/ABA/time boundaries. No fee version system and no budget condition added to `adopt`
- New visited POIs are identified by ID difference between real timelines. Only those tickets can be edited inside preview. Common tickets/meals/lodging/other remain editable in the formal panel and update both sides. A shared `CostEstimateInput` keeps the same validation/units. Unknown new ID stays blank; same-ID historical session estimate can be reused, with address/ID visible to distinguish same-name places
- Cancel removes only the adjustment; actively entered estimates remain in result-level state, excluded from formal costs until their POI is actually arranged. The cancel message explicitly confirms this. Adoption uses the already-viewed route response, with no HTTP or fee merge/copy; formal totals become the preview-side totals for identical current estimates. Unknown, invalid or exceeded money does **not** disable otherwise valid itinerary adoption. Edit/new result/refresh discards fee state through the existing result lifecycle
- The UI retains both route-generation batches, warns that different query batches can change reference fares independently of removing a place, and does not call a lower known subtotal guaranteed savings or sufficient budget. No automatic reduction of meal/lodging totals, no inferred free tickets, no cheapest-place optimization

### Delivery manifest — 12 files

1. `frontend/src/components/trip-plan-result.tsx` — shared result-level estimate state/callback, passed to both views
2. `frontend/src/components/trip/schedule-budget.tsx` — controlled inputs, original formal-budget rules retained
3. `frontend/src/components/trip/cost-estimate-input.tsx` — reusable ID/date/group-total estimate field and validation display
4. `frontend/src/components/trip/schedule-cost-comparison.tsx` — old/new subtotals, unknowns, signed difference and new-visit inputs
5. `frontend/src/components/trip/schedule-adjustment.tsx` — active-preview-only comparison and guarded edits
6. `frontend/src/lib/schedule-costs.ts` — shared input update and pure dual invocation/difference helper
7. `frontend/src/lib/use-schedule-adjustment.ts` — existing-session event guard and explicit cancel/estimate retention message
8. `frontend/src/lib/schedule-cost-comparison.test.ts` — 15 pure comparison/scope/precision/unknown tests
9. `frontend/src/components/trip/schedule-budget.test.tsx` — controlled parent harness preserves existing panel coverage
10. `frontend/src/components/trip/schedule-budget.integration.test.tsx` — shared inputs, cancel/adopt, invalidation and original module regression
11. `frontend/src/lib/use-schedule-adjustment.test.tsx` — synchronous current-preview guard, ABA and time-boundary coverage
12. `docs/PROGRESS.md` — actual implementation, evidence, limits and closeout

### Automated checks executed this round

| Command in `frontend` unless noted | Result |
| --- | --- |
| `npm test` | **904 tests / 35 files passed**; 30 new tests, existing coverage retained |
| `npm run lint` | Passed |
| `npm run typecheck` | Passed (`next typegen && tsc --noEmit`) |
| `npm run build -- --webpack` | Passed; final build used for Chrome acceptance |
| `git diff --check` (project root) | Passed |

Tests cover positive/negative/zero differences, exact signed safe-cent limits, one/two-sided unknowns and identical unknown labels, invalid current values/aggregate overflow, same-name/different-ID costs, partial/empty/required-incomplete distinctions, both-mode reference fare batches, new-only/common estimate propagation, cancel/reopen/adopt equality, ordinary regeneration, no added requests, active preview during cost edits, input/weather/candidate/annotation/midnight expiry, stale handlers/ABA and existing late response/Marker regressions. The old 5D-1 assertion that the **entire** preview text stays unchanged on fee edits was intentionally narrowed to unchanged itinerary differences: the new fee portion must update. No backend change, so no backend full-suite rerun or historical result relabeled as new

### Actual Chrome acceptance — stable evidence

Real Chrome **155.0.8059.39**, 2026-10-10 approximately 17:53–17:58 Asia/Shanghai. Actual production frontend `http://localhost:3000`, existing backend `http://127.0.0.1:8000`; real POIs, route responses and forecast, no interception or Mock substitute. One-day request for 2026-10-10, 09:00–18:00, three travelers, CNY 3,000 total, accommodation reference 静安寺, required 武康大楼, photography candidates. All manual ticket/meal/lodging examples below are **user estimates**, not verified prices

Stable directory: [/Users/zijing/projects/trippilot-evidence/5d-2/](/Users/zijing/projects/trippilot-evidence/5d-2/). All **11 screenshots** were individually opened and visually inspected. The [sanitized receipt](/Users/zijing/projects/trippilot-evidence/5d-2/acceptance-receipt.json) stores projected paths/status/times/counts, fare occurrences and necessary assertions; no credentials, headers, SDK URLs, coordinates or raw request/response bodies. Evidence stays outside the repository

| Actual check | Observation | Screenshot |
| --- | --- | --- |
| Walking, new fee unknown | Original includes 延中广场公园, preview adds 辅德里公园. Original **815.50**, preview known **795.50** with new ticket unknown: complete difference unavailable, not savings. Loading had no comparison | [01 unknown](/Users/zijing/projects/trippilot-evidence/5d-2/01-walking-new-place-unknown.png) |
| New vs common edits | New ticket 30/20/10 gives +10/equal/−10 respectively without changing formal budget. Changing shared lodging 600→650 updates original to **865.50**, preview to **855.50**, delta −10. Adoption stays enabled; edits add zero requests | [02 shared estimates](/Users/zijing/projects/trippilot-evidence/5d-2/02-shared-estimates-and-difference.png) |
| Cancel/reopen | Cancel retains original **865.50**, same itinerary and shared lodging; no request. Another explicit preview reuses new-place estimate **10** rather than blank/inheriting old-place 20 | [03 cancel](/Users/zijing/projects/trippilot-evidence/5d-2/03-cancel-official-unchanged.png), [04 reopen](/Users/zijing/projects/trippilot-evidence/5d-2/04-reopened-retained-estimate.png) |
| Adopt | Formal subtotal becomes **855.50**, exactly the viewed preview side; preview disappears, new ticket 10 preserved, no additional request | [05 adopted](/Users/zijing/projects/trippilot-evidence/5d-2/05-adopted-matches-preview.png) |
| Real transit comparison | Original actual 548/911/01 bus movements each 2 × 3 travelers: **18** transport, **873.50** subtotal. Preview actual 548/48 bus movements: **12** transport, **857.50** subtotal, delta **−16**. Only timeline movements count. Valid required-only partial remains compare/adopt eligible; not a subway/transfer-specialist test | [06 bus comparison](/Users/zijing/projects/trippilot-evidence/5d-2/06-transit-real-cost-comparison.png) |
| Actual optional-route limitation | Preview's 静安雕塑公园、人民公园-相亲角、苏河湾亲水平台 optional trials each reported route data error; none added. Required visit and return remain valid, so compared the actual retained scope. No fallback, fabricated fare or relaxed validation | [08 optional reasons](/Users/zijing/projects/trippilot-evidence/5d-2/08-transit-optional-not-arranged.png) |
| Narrow screen | 400px viewport/document width, vertically stacked old/new amounts and qualifications, no horizontal overflow | [07 400px](/Users/zijing/projects/trippilot-evidence/5d-2/07-400px-cost-comparison.png) |
| Weather expiry | One explicit refresh immediately removes comparison/editor, disables adoption, keeps old timelines viewable and formal budget unchanged. Success does not revive old preview | [09 expired](/Users/zijing/projects/trippilot-evidence/5d-2/09-weather-expired-comparison.png) |
| Old map/Marker | Real binding and separate walking route **2,695 m / 2,156 s**, visible road basemap/polyline. Activity→静安寺 centers/highlights; clicking 武康大楼 Marker selects its activity and clears first highlight. Budget unchanged and Marker interaction makes zero business requests | [10 activity→Marker](/Users/zijing/projects/trippilot-evidence/5d-2/10-map-activity-marker.png), [11 Marker→activity](/Users/zijing/projects/trippilot-evidence/5d-2/11-map-marker-activity.png) |

Observed **14 browser-to-local-backend requests**, all HTTP 200: `/places/search` 4, `/trips/plan` 1, `/places/candidates` 1, `/trips/schedule-preview` 5 (walking original, cancelled preview, adopted preview, transit original, transit preview), `/weather/forecast` 2, `/routes/walking` 1. Fee edits, cancel, adopt and Marker operations add no requests. These local counts are not per-attempt upstream observations. Weather was day 多云/night 晴 (initial publication 2026-10-10 17:33:13 +08:00); adjustment was manual, not an asserted real rain-day test

### Limitations and closeout

- Live transit comparison succeeded for the **required-only partial** described above, not successful new optional insertion; the actual `data_error` limitation is retained without speculating about the raw upstream cause. Live adoption was walking. Transit adoption, same-name collision, monetary overflow/unknown fares, failures, expiry boundaries and ABA are covered by controlled tests or earlier records, not relabeled as new real samples
- Estimates are not quotes, verified admission/room rates or guaranteed spending. Dedicated real subway/transfer samples, physical-phone/touch and six-required-plus-three-optional full-load remain pending. Existing future-date timetable, batch-local pacing, shared search deadline/cancellation-cleanup, Turbopack and security-advisory limits remain unchanged in historical records
- Authorized ordinary commit/push of exactly the 12 delivery files after manifest, whitespace, sensitive-information and remote-divergence checks. No cache/env/build/evidence files, force push, reset, amend or next milestone. Commit SHA and push outcome belong in Git history and final handoff

## Milestone 5D-1 Actual Implementation and Acceptance (2026-10-10)

### Baseline and scope

- Actual project `/Users/zijing/projects/trippilot`, branch `main`, starting HEAD `5a11561b21076f19e960e6511855ed9ec59bf830` (accepted and pushed 5C-3). Final `git fetch origin main` confirmed that same remote HEAD, zero ahead/behind before this commit, with no divergence. No temporary project copy. Five pre-existing Python caches are preserved by content hash, not staged or cleaned
- Independent **正式草案费用与预算** panel beside the real schedule, separate from the existing clearly marked Mock budget. Only the current official `ScheduleResponse` and submitted trip budget/traveler count supply scope. There is no second stored schedule, backend change, new endpoint/request/dependency, persistence, automatic exclusion/adoption, or scheduling change
- Existing backend service was reused. The local frontend was restarted with this round's successful Webpack production build for actual Chrome acceptance. Environment files, credentials, proxy and CORS were not modified

### Calculation rules and state boundaries

1. `schedule-costs.ts::calculateScheduleCosts` first validates the official schedule and requested dates. No valid draft or no actual `visit` means no amount/sufficiency conclusion. Real schedule dates drive all daily meal fields, including empty days; old Mock days, activity costs, `estimated_cost` and `budget_breakdown` never enter this calculation
2. Iterate every actual `walk`/`transit` item in `days[].items`, then resolve its validated `edge_id`. Each occurrence is charged, even when the same cached edge is referenced again. Unused trial edges are ignored. The full transit scheme's `fare_cny` is counted once per occurrence, multiplied by travelers; walking/access/transfer legs are not separately added. Walking and same-place ticket costs are rule-zero, not verified free sightseeing. Null fare stays unknown and has no manual override. Each row retains date, endpoints, times, edge reference, source and query time
3. All estimate fields start blank. Tickets belong to actually visited POI IDs, meals to each requested date, lodging/other to the whole trip. Each is an all-traveler user estimate, never multiplied again. Same-name/different-ID places remain separate. Same-ID text can survive within the result session, explicitly still the user's original estimate, not a fresh quote; removed places no longer contribute and newly arranged places are unknown. Lodging references provide neither room rates nor inferred nights
4. User strings accept non-negative decimal money with at most two fraction digits. Blank is unknown; explicit `0` is known but not verified free. Invalid text/negative/extra precision/unsafe magnitude immediately invalidates the conclusion, without retaining a previous valid value or truncating. Conversion and multiplication/sums use `BigInt`, bounded to `Number.MAX_SAFE_INTEGER` **cents**, then expose safe integer cents. Formatting also uses integer division/remainders. Decoded JSON budget/fare numbers use their canonical decimal representation and must convert to an exact whole cent; no rounding (e.g. `0.29 → 29`, `1.005` and `0.1 + 0.2` rejected). This policy does not claim to recover precision already lost before JSON was decoded
5. Separate known transport subtotal, entered-estimate subtotal, known combined subtotal, missing costs and budget difference. Unknowns do not count as zero-valued facts; the difference is explicitly not freely spendable money. An already-over-budget known portion can be flagged while missing items remain. Filled estimates still do not guarantee actual expenditure. Unarranged required visits restrict the conclusion to the arranged portion; unarranged optionals are outside scope, not missing costs
6. `ScheduleBudget` stores only input text and derives its display directly from official response props. Preview loading/failure/completion/cancellation leave official costs unchanged. Successful adoption naturally recalculates the scope and retains meals/lodging/other. Ordinary draft invalidation hides the old totals but keeps session estimates. Leaving the result and refreshing discard them. Fee editing does not invoke shared revision invalidation, candidate exclusion, weather requests/checks, preview invalidation or map changes; these estimates deliberately do not participate in planning decisions

### Delivery manifest — 7 files

1. `frontend/src/lib/schedule-costs.ts` — pure safe-cent parsing, transport-occurrence and user-estimate calculation
2. `frontend/src/lib/schedule-costs.test.ts` — arithmetic, scope, repeated-edge, unknown and boundary tests
3. `frontend/src/components/trip/schedule-budget.tsx` — independent result-local estimate inputs and traceable budget panel
4. `frontend/src/components/trip/schedule-budget.test.tsx` — field states, provenance, unknowns, adoption and lifecycle tests
5. `frontend/src/components/trip/schedule-budget.integration.test.tsx` — actual result composition, adjustment/candidate/weather/map independence
6. `frontend/src/components/trip-plan-result.tsx` — mount the panel against the current successful official draft only
7. `docs/PROGRESS.md` — actual rules, checks, evidence and closeout record

### Automated checks executed this round

| Command in `frontend` unless noted | Result |
| --- | --- |
| `npm test` | Passed: **874 tests / 34 files**, including 59 new cost tests; existing regressions retained |
| `npm run lint` | Passed |
| `npm run typecheck` | Passed (`next typegen && tsc --noEmit`) |
| `npm run build -- --webpack` | Passed; this production build was used for Chrome acceptance |
| `git diff --check` (project root) | Passed |

Controlled tests cover null/zero/invalid fares, unknown vs explicit zero, decimal precision, multiplication/category/grand-total overflow, repeated directed-edge occurrences, unused edges, scheme-vs-leg billing, same-place movements, same-name/different-ID tickets, cross-year three-day meals including empty days, partial required/no-visit scope, preview timeout/cancel/adoption, cost edits during a pending preview, ordinary invalidation/regeneration and result remount. No backend source changed, so no backend suite was rerun or claimed as a new result. No business changes were made after the successful production checks

### Actual Chrome acceptance and stable evidence

Real Chrome **155.0.8059.39**, 2026-10-10 approximately 15:43–15:53 Asia/Shanghai, production UI `http://localhost:3000`, existing backend `http://127.0.0.1:8000`. Actual Shanghai POI searches, candidate search, walking/transit estimates and weather; no API/SDK interception or fabricated upstream response. One-day requirement: 2026-10-10, 09:00–18:00, three travelers, CNY 3,000 total, accommodation reference 静安寺, required 武康大楼. Monetary fields below are deliberate **user estimate examples**, not claims about real admission/hotel/meal prices

Stable directory: [/Users/zijing/projects/trippilot-evidence/5d-1/](/Users/zijing/projects/trippilot-evidence/5d-1/). Each of the **14** screenshots below was opened and visually inspected after saving. Minimal [sanitized receipt](/Users/zijing/projects/trippilot-evidence/5d-1/acceptance-receipt.json) contains only request paths/status/times/counts, necessary fare arithmetic and observed assertions; no raw bodies, credentials, headers, coordinates, SDK URLs or sensitive network logs. Evidence remains outside Git

| Actual check | Observation | Evidence |
| --- | --- | --- |
| Walking + initial unknowns | Three actual movements including lodging return; transport rule-zero, all five manual fields unknown, no claim of sufficient budget | [01 walking/blank](/Users/zijing/projects/trippilot-evidence/5d-1/01-walking-blank-estimates.png) |
| All-traveler estimates | Tickets 15.50/20, meals 180, full lodging 600, other 0 give **815.50**, not multiplied by three travelers | [02 entered estimates](/Users/zijing/projects/trippilot-evidence/5d-1/02-walking-user-estimates.png) |
| Missing + exceeded + invalid | Clearing meals and setting other 3,000 gives known **3,635.50**, exceeds by **635.50**, meals still unknown. `1.001` immediately suppresses totals/difference rather than retaining old results; correcting restores valid calculation | [03 partial excess](/Users/zijing/projects/trippilot-evidence/5d-1/03-over-budget-with-unknown.png), [04 invalid precision](/Users/zijing/projects/trippilot-evidence/5d-1/04-invalid-precision-no-old-total.png) |
| Adjustment preview/cancel | Real preview removes 延中广场公园 and adds 辅德里公园. Loading and completed preview leave the official budget unchanged. Editing other to 5 leaves adoption enabled; cancellation retains the updated official **820.50** and sends no request | [05 preview](/Users/zijing/projects/trippilot-evidence/5d-1/05-adjustment-preview.png), [06 cancelled/original](/Users/zijing/projects/trippilot-evidence/5d-1/06-cancel-retains-official-budget.png) |
| Confirmed adoption | Second explicit preview then adoption: no adoption HTTP request; old optional ticket 20 no longer contributes, new ticket blank/unknown; required/meal/lodging/other values retained, known subtotal **800.50** | [07 adopted budget](/Users/zijing/projects/trippilot-evidence/5d-1/07-adopt-new-place-unknown.png) |
| Transit actual fare accounting | Switching mode clears the official budget conclusion without HTTP. Explicit generation yields actual **548 路、911 路、01 路**, each scheme **2 yuan × 3 travelers = 6**, total **18**; no additional access-walking charge. Known subtotal **818.50**, new optional ticket initially unknown. No dedicated subway/transfer claim | [08 transit](/Users/zijing/projects/trippilot-evidence/5d-1/08-transit-three-travelers.png) |
| 400px + explicit zero | New optional ticket entered as 0 shows user estimate, not verified free. Viewport and document width both 400; readable vertically stacked traffic, fields and summary | [09 traffic](/Users/zijing/projects/trippilot-evidence/5d-1/09-400px-transit-details.png), [10 inputs](/Users/zijing/projects/trippilot-evidence/5d-1/10-400px-user-estimates.png), [11 summary](/Users/zijing/projects/trippilot-evidence/5d-1/11-400px-summary.png) |
| Old map/Marker regression | Bound old Mock activities to the two real POIs; separate real walking route **2,695 m / 2,156 s** shown. Activity→静安寺 Marker centered/highlighted; click 武康大楼 Marker selects corresponding activity and restores first Marker. Basemap, road labels and polyline visible. Budget unchanged; subsequent cost edit leaves Marker/route unchanged and sends zero requests | [12 activity→Marker](/Users/zijing/projects/trippilot-evidence/5d-1/12-map-activity-selected.png), [13 Marker→activity](/Users/zijing/projects/trippilot-evidence/5d-1/13-map-marker-selected.png) |
| Session reset | Returning to edit keeps confirmed requirements; resubmitting and generating required-only walking draft gives all four cost fields blank. Subsequent browser reload leaves no result/budget and makes zero business requests | [14 new result](/Users/zijing/projects/trippilot-evidence/5d-1/14-new-result-clears-estimates.png), receipt |

Observed browser-to-local-backend business requests: **14 total, all HTTP 200** — `/places/search` 4, `/trips/plan` 2, `/places/candidates` 1, `/trips/schedule-preview` 5 (initial walking, cancelled-preview batch, adopted-preview batch, transit, new-result walking), `/weather/forecast` 1, `/routes/walking` 1. Cost entry/correction, cancellation, adoption, viewport/Marker changes and reload add no business requests. These are **not** counts of every upstream AMap HTTP attempt. Weather query preserved the budget; this sample was daytime 多云/night 晴, so adjustment was an explicit manual choice, not rain-driven live acceptance. Chrome reported no console errors and two warnings during map regression

### Remaining limits and closeout

- Unknown fares, repeated edges, overflow, empty dates, missing required visits, preview timeout and related state independence branches are **controlled tests**, not claims of live provider failures. No fault was manufactured. Real adoption was walking; real non-null-fare calculation was bus transit; transit-specific adoption and dedicated real subway/transfer samples were not separately re-run
- This is a cost reference, not booking/price verification: children/concessions/passes, travel-day tariffs, ticket availability and lodging prices remain unverified. No unknown cost is silently zero; no amount triggers exclusion, adoption or schedule changes
- Preserve prior limits: physical-phone/touch pending; prior transit `data_error` sample remains documented; future-day service/timetable/transfer feasibility is not validated; six-required-plus-three-optional live full-load not accepted; batch-local pacing is not account-wide; 20 seconds is a search deadline with additional cancellation cleanup possible; browser counts do not prove upstream attempt counts; Turbopack restriction/dependency advisories remain below
- Authorized normal commit/push after final explicit seven-file staging and remote-divergence check. Cache/environment/build artifacts/external evidence excluded. No reset/amend/force push or next milestone. Exact commit/push outcome is reported in Git history and final handoff rather than rewriting historical acceptance

## Milestone 5C-3 Actual Implementation and Acceptance (2026-10-10)

### Baseline, scope and closeout

- Actual workspace `/Users/zijing/projects/trippilot`, branch `main`, starting HEAD `ec558900d2a037d334910589edf0c7596b2d29f6` (accepted/pushed 5C-2). `git fetch origin main` confirmed the same remote HEAD, with no divergence or preceding unpushed commits. Remote: `https://github.com/zij30548/TripPilot.git`
- Upgrade the weather-check optional action to **预览排除该地点后的草案**. Ordinary candidate exclusion/restoration remains immediate and clearly distinguished. Required POIs cannot be preview-exclusion targets. No backend, dependency, schema, algorithm, configuration, secret, CORS or proxy changes; no temporary project copy, persistence, automatic replacement or indoor inference
- Accepted for the authorized ordinary commit and normal push with `feat: preview and adopt optional-place schedule adjustments`. No force push/reset/amend. Exact commit/push result belongs in Git history and the final handoff; do not treat historical then-unpushed records below as present state. Stage exactly the 18 delivery files, not caches, environment files, generated artifacts or external evidence

### Data flow and correctness boundaries

1. `use-weather-schedule-check.ts` exposes a read-only, action-time `canAdjustNow` guard instead of directly excluding. It rechecks report identity/revision, actual optional visit/POI identity, current candidate eligibility and Shanghai time/freshness. Rechecking identical inputs creates a new report authorization; old handlers cannot act on the replacement
2. `use-schedule-preview.ts::capture` validates and clones the original response, full request, current candidate batch/order/exclusions and settings including historical stays. `use-schedule-adjustment.ts` also freezes weather/report identity and provenance. `schedule-input.ts` shares the ordinary candidate selection, validation, request building and duration defaults, avoiding a second first-three rule
3. `schedule-adjustment.ts::prepareAdjustmentInput` adds only the target ID to a private exclusion set, selects the remaining eligible first three in pool order, and preserves all fixed requirements/mode/lunch plus existing stay sources. A newly entering POI without a saved setting gets 60/default. No-required/no-optional input is rejected without HTTP. Preview creation, completion, failure and cancellation never publish these temporary settings or exclusions
4. The independent adjustment hook reuses `querySchedulePreview` and `isScheduleResponse` with its own pending flag, AbortController, session identity and version. One explicit click sends one existing schedule request; no automatic query/retry, POI fetch, weather refresh or transport fallback. The official response remains visible while preview loading, failed or ready
5. `compareSchedules` compares actual visits by POI ID and date, retaining same-name/different-ID distinctions. It shows additions/removals, retained required/optional date/start/end changes, nullable daily returns and both query generation times/modes. Original and proposed full timelines reuse `ScheduleResult`, including required completion and optional/unarranged reasons. No absent return becomes midnight; no traffic sum is fabricated. Different query batches can change estimates independently of the exclusion
6. Adoption checks full request/response compatibility, unchanged fixed requirements/required order, absence of the target, all required visits scheduled and at least one actual visit. Existing strict validation also checks chronology, lunch, edge references, mode and verified daily returns. A valid **partial** is adoptable when all required visits fit; adding no new optional does not itself block adoption. Empty or required-incomplete results remain inspectable but cannot be adopted
7. `use-candidate-pool.ts::prepareExclusion` creates a read-only transaction token tied to the current snapshot identity. `use-schedule-preview.ts::adoptAdjustment` preflights that token, input version, reconstructed request and response, and clones all adoption data before committing. In one synchronous operation it publishes the official exclusion through the existing invalidation path, updates candidate/settings refs and rendered state, and installs the exact viewed response. No effect/timer restores a cleared response and no network request occurs during adoption. The session is consumed first to reject double-clicks/old events
8. Existing shared result-local revision now has synchronous invalidation subscribers. Candidate changes, rule/mode changes, ordinary generation, weather refresh, report replacement, exposure changes, edit and time-boundary changes immediately abort/expire adjustment state. Old success/error/finally cannot revive it, including ABA and clock reversal. Cancellation only removes the preview, never overwrites intervening user inputs. Weather refresh preserves the official draft. Adopt invalidates the weather check; same-ID labels remain, new IDs default to unknown. Mock dates, old-map modes/bindings/routes/Markers remain independent

The existing 25-second frontend timeout and backend budgets are unchanged: selected transport only; up to 17/34 required-only or 29/58 optional-inclusive logical edges/bounded HTTP attempts; one 20-second search deadline shared across phases, at most three concurrent calls and 0.4-second logical startup spacing. Cancellation cleanup can increase return time; pacing applies only within this request, not account-wide. Preview requests are new batches, not persistent cached versions. The feature is explicit user review/commit, not an optimal or automatically weather-safe replacement

### Delivery manifest — 18 files

1. `frontend/src/lib/schedule-input.ts` — shared optional selection, input validation/building and isolated stay defaults
2. `frontend/src/lib/schedule-adjustment.ts` — frozen input preparation, adoption eligibility and ID/date differences
3. `frontend/src/lib/use-schedule-adjustment.ts` — independent request lifecycle, expiry, cancellation and adoption coordination
4. `frontend/src/lib/use-schedule-preview.ts` — reuse input helpers, capture validated state, synchronous adopted input/response installation
5. `frontend/src/lib/use-candidate-pool.ts` — prepared exclusion transaction; ordinary exclusions/restoration retained
6. `frontend/src/lib/use-weather-schedule-check.ts` — action-time authorization, synchronous revision subscribers and new report identity
7. `frontend/src/components/trip/schedule-adjustment.tsx` — comparison, nullable returns, full original/preview timelines, adopt/cancel UI
8. `frontend/src/components/trip/schedule-preview.tsx` — extract reusable read-only `ScheduleResult`, retaining the existing settings panel
9. `frontend/src/components/trip/weather-schedule-check.tsx` — explicit preview action and non-automatic adjustment copy
10. `frontend/src/components/trip/candidate-preparation.tsx` — distinguish ordinary immediate exclusion/restoration from preview
11. `frontend/src/components/trip-plan-result.tsx` — result-local composition and lifecycle wiring
12. `frontend/src/lib/schedule-adjustment.test.ts` — preparation/eligibility/difference tests, including same-name identities and nullable returns
13. `frontend/src/lib/use-schedule-adjustment.test.tsx` — both modes, official-state preservation, adoption consistency, cancellation/late responses, ABA and time checks
14. `frontend/src/lib/schedule-adjustment.test-fixtures.ts` — strictly validated controlled complete/partial/empty walking/transit responses
15. `frontend/src/lib/use-weather-schedule-check.test.tsx` — guard and ordinary-exclusion regressions under the new preview boundary
16. `frontend/src/lib/weather-schedule-check.test-fixtures.ts` — explicit shared candidate fixture type
17. `frontend/src/components/trip/weather-schedule-check.integration.test.tsx` — real parent composition: comparisons/full timelines, private preview/cancel/adopt and old-map isolation
18. `docs/PROGRESS.md` — current results, manifest, evidence and limitations; historical records below preserved

### Automated checks executed this round

| Command (frontend unless noted) | Actual result |
| --- | --- |
| `npm test` | **31 files / 815 tests passed**, final run 2026-10-10 15:07 Shanghai; existing route, SDK/Marker, candidate, schedule, weather and cross-language contract suites included |
| `npm run lint` | Passed |
| `npm run typecheck` | Passed (`next typegen && tsc --noEmit`) |
| `npm run build -- --webpack` | Passed; actual Chrome used this production build |
| `git diff --check` (root) | Passed; staged check repeated during closeout |
| Backend suite | **Not rerun**: backend source unchanged; historical backend totals are not this round's executions |

New controlled tests cover fourth-candidate promotion, historical/new duration sources, preview isolation, explicit single request, success/error/timeout/invalid payload/cancellation, older success/error/finally versus a new pending preview, stale cancel/adopt, mode/candidate ABA, settings/weather/report/edit invalidation, midnight/staleness click-time checks and clock reversal, unmount, required-only partial adoption, empty/incomplete rejection, and identical input on the next ordinary generation. Both walking and transit use the unchanged strict parser. Initial test-selector ambiguity and fixture literal-type errors were corrected before the passing final checks; no validation was relaxed for live acceptance

### Real Chrome acceptance and stable evidence

Actual Chrome **155.0.8059.39**, `http://localhost:3000`, reused FastAPI `127.0.0.1:8000` without restarting or modifying it. Only the existing frontend was restarted for the passing Webpack build. Initial sandbox port-listen denial was resolved through approved local-service execution, not configuration changes

Evidence directory: [`/Users/zijing/projects/trippilot-evidence/5c-3/`](/Users/zijing/projects/trippilot-evidence/5c-3/). **All 15 screenshots were individually opened and checked.** [Sanitized receipt](/Users/zijing/projects/trippilot-evidence/5c-3/acceptance-receipt.json) contains path-only business-request metadata, normalized schedule/weather summaries, UI observations and screenshot/source hashes; no SDK URLs, headers, upstream raw logs or credentials

One-day request **2026-10-10, 09:00–18:00**, reference 静安寺 (`B00154BDE9`), required 武康大楼 (`B00155ITJ1`), photography candidates. Real forecast publication **2026-10-10 15:01:16 +08:00** covered the requested day: **day 多云 26°C / night 晴 18°C**. No rain/snow triggered, so this was **explicit manual user adjustment**, not live rain-remediation acceptance

| Acceptance | Observation | Evidence |
| --- | --- | --- |
| Original draft and local check | 武康大楼 09:36–10:36; optional 延中广场公园 13:00–14:00; return 14:44. Explicit outdoor label and check caused zero requests | [01 check](/Users/zijing/projects/trippilot-evidence/5c-3/01-original-weather-check.png), [02 original timeline](/Users/zijing/projects/trippilot-evidence/5c-3/02-original-timeline.png) |
| Private preview and comparison | Official draft/candidate text unchanged during loading and after completion. Proposed 延中 exclusion promoted fourth candidate into input; actual added visit was 辅德里公园, still unknown exposure. Required time unchanged, return 14:44→14:28. Both full timelines opened | [03 comparison](/Users/zijing/projects/trippilot-evidence/5c-3/03-preview-comparison.png), [04 preview timeline](/Users/zijing/projects/trippilot-evidence/5c-3/04-preview-timeline.png) |
| Cancel | Zero additional requests; original draft/candidate text remained identical; no temporary fourth-candidate settings published | [05 canceled notice](/Users/zijing/projects/trippilot-evidence/5c-3/05-cancel-original-retained.png), receipt identity observations |
| Explicit second preview and adopt | Second query returned valid **partial**, all required arranged. Adopt generated zero requests, used the displayed generation timestamp/timeline, excluded 延中 in official pool, and invalidated the check | [06 second preview](/Users/zijing/projects/trippilot-evidence/5c-3/06-second-preview-to-adopt.png), [07 adopted timeline](/Users/zijing/projects/trippilot-evidence/5c-3/07-adopted-timeline.png), [08 official candidates](/Users/zijing/projects/trippilot-evidence/5c-3/08-adopted-candidates.png) |
| Following ordinary generation | Captured POST body exactly matched the adopted preview request: 辅德里／静安雕塑／人民公园-相亲角; excluded target absent, same required/mode/lunch/durations | Receipt request/identity checks |
| Real unavailable route safely retained original | A later preview for 辅德里 returned HTTP 200 / `unscheduled`, with required outbound edge `failed` and return edge `ok`. Required visit absent: adoption disabled, original draft/candidate exclusion unchanged; missing return displayed as unavailable, not 00:00. Specific upstream failure cause not inspected. An explicit cancel/new-preview click later succeeded; no automatic retry or walking fallback | [09 non-adoptable 400px](/Users/zijing/projects/trippilot-evidence/5c-3/09-preview-400px.png), receipt batches 5/6 |
| Narrow-screen comparison | Both non-adoptable and adoptable comparisons checked at **400×900**, document width **400**. Full text/actions, vertical old/new layout and unknown new-place exposure visible | [10 adoptable 400px](/Users/zijing/projects/trippilot-evidence/5c-3/10-adoptable-preview-400px.png) |
| Existing map regression and isolation | Explicitly bound two old Mock activities to 静安寺/武康大楼; real walking 2,695m / 2,156s, bottom map/roads/labels/blue polyline visible. Activity selects/centers highlighted Marker. After stable-viewport pointer click, Marker selects/highlights 午餐与休息 and previous Marker returns normal. Draft/ready preview remained identical, adopt stayed enabled; only two explicit POI searches and one walking request added | [11 route](/Users/zijing/projects/trippilot-evidence/5c-3/11-old-map-walking-route.png), [12 activity→Marker](/Users/zijing/projects/trippilot-evidence/5c-3/12-activity-to-marker.png), [14 confirmed Marker→activity](/Users/zijing/projects/trippilot-evidence/5c-3/14-marker-to-activity-confirmed.png) |
| Weather refresh expiry | Adopt became disabled during refresh and stayed disabled afterward; current official draft unchanged. Exactly one weather request, no extra schedule query. Old preview retained only as expired inspection data | [15 expired preview](/Users/zijing/projects/trippilot-evidence/5c-3/15-weather-refresh-expired.png) |

Screenshot [13 first Marker attempt](/Users/zijing/projects/trippilot-evidence/5c-3/13-marker-to-activity.png) is retained **as an unsuccessful click observation**, not a passing Marker test: the first fit/scroll/click did not change selection. After waiting for the stable viewport and verifying the pointer hit the actual Marker, a new real click passed (14). No synthetic event, SDK mock or source change was used to claim success

Actual local business requests: **4 POI search, 1 Mock-plan submission, 1 candidate batch, 6 schedule-preview, 2 weather, 1 old-map walking**. The six schedule requests were original, first preview/cancel, second preview/adopt, following ordinary generation, the non-adoptable third preview, and an explicit subsequent preview. Cancellation/adoption/check/annotation did not query. These browser counts **do not measure individual upstream HTTP attempts**. Console: zero errors, one Canvas2D readback-performance warning; all observed business responses HTTP 200, with the one semantic route failure explicitly recorded rather than hidden

### Limits and interview explanation

- Live adjustment acceptance used **walking**, one day, one required and three optional inputs. Transit-mode preview/adoption, multi-day time changes, required-only/no-visit results, timeout/invalid-payload/ABA/midnight and rain/snow branches have controlled coverage, not new live specialty acceptance. The observed live generic route failure is not proof of any particular upstream/network failure category
- Physical phone/touch, dedicated real subway/transfers, six-required-plus-three-optional live load, broader real faults and Turbopack remain unverified/limited as documented historically. The old Oriental Pearl `data_error` record remains unchanged; no fallback/relaxed validator was introduced. No guarantee of future weather, operating hours, reservations, budget or transit timetables
- This is a **prepare → review → commit** interaction: request and display a private validated copy, compare it using stable POI identities, then commit only if every dependency version still matches. Abort saves unnecessary work; versions reject late success/error/finally and ABA. Synchronous preflight/commit keeps official exclusions, internal settings/candidate refs and visible response consistent. The same candidate-selection/request/strict-response/timeline logic is reused; no second scheduler or automatic weather recommendation is introduced

## Milestone 5C-2 Actual Implementation and Acceptance (2026-10-10)

### Baseline, scope and closeout

- Actual project `/Users/zijing/projects/trippilot`, `main`, starting HEAD `454d7939c3a730a47399b50431ab1e1850a87676`. The existing local commits `16e68ff8d02ef698bcf472c1395b455aaba1bc0b`, `01224f166a5ff11057b4d522b6be0f4c579b877d` and `454d7939c3a730a47399b50431ab1e1850a87676` are preserved, not reset/amended. Origin is `https://github.com/zij30548/TripPilot.git`; refreshed `origin/main` remains `e917000c140e3f9fedb3a3b9adaa17b03fb75244`, an ancestor, before the authorized new closeout
- User authorizes ordinary commit and normal push only after checks/acceptance pass. Stage precisely the 13 files below, excluding all five original Python caches, secrets/environment files, build artifacts and external evidence. Commit message: `feat: add weather-aware schedule checks and optional adjustments`. Check the final staged manifest, whitespace and secrets and all four pending commits before pushing; report the actual resulting SHA/push status in the handoff
- No backend, API, algorithm, dependency, proxy, key or CORS modification. The existing frontend production service was restarted on its original port to serve the new Webpack build; the existing backend on 8000 was reused. No temporary project copy

### Data flow, rules and design tradeoffs

1. `TripPlanResult` owns the new check next to the existing real draft. Only `visit` items actually present in `ScheduleResponse.days` participate. Their POI snapshots and required/optional identity come from that response's request by **ID**, not names, unscheduled candidates, Mock dates/activities or map bindings. Empty days do not acquire invented visits
2. A result-local map keyed by POI ID stores the user's **户外为主 / 室内为主 / 不确定** setting; unknown is the default. No inferred POI attributes. Same-ID annotations survive draft regeneration; newly scheduled POIs remain unknown. Editing the overall request clears this map synchronously; unmount/refresh removes it. No persistence
3. Explicit **检查天气对行程的影响** calls `checkWeatherSchedule` with draft, the existing normalized weather batch, annotations and an explicit current time. It is a pure function with no fetch and no input mutation. It reuses existing exact-description precipitation rules, Shanghai dates and publication freshness. Day/night remain separate; one known half survives an unknown half. It associates the same date, never invents an hourly day/night cutoff or matches an activity to precise weather hours
4. Outdoor + same-day rain/snow calls attention to the relevant period, not necessarily the activity's actual time. Indoor is explicitly the user's setting and does not establish transport immunity; unknown exposure cannot establish exposure. No triggered precipitation rule is not a suitability guarantee. Missing coverage, stale/unknown publication, past dates or unknown descriptions leave the affected evidence **未评估**. It does not assess transport, opening, reservations or safety, or automatically change an itinerary
5. A result-local monotonic revision is invalidated synchronously inside existing weather/schedule cancellation paths. Existing `candidates.exclude() → schedule.updateCandidates()` also invalidates it, without a second exclusion store. Weather query start invalidates the check but preserves the draft. Candidate refresh/exclude/restore and schedule settings/generation invalidate the check along with their existing draft behavior. User annotation changes invalidate only the check. Old reports remain visibly invalid, with unassessed current summaries and disabled adjustment actions
6. A report captures that revision and its own identity. Exclusion rechecks the current revision, report identity, current clock, actual scheduled optional ID and current optional/non-excluded candidate status at the event boundary. Old closures and A→B→A cannot authorize actions on a newer report/draft. The shared network-free display clock invalidates at Shanghai midnight, the >24-hour freshness boundary, visibility restoration and focus. An exclusion checks time again even before the next render/tick
7. Explicit optional exclusion explains **whole-trip** removal, draft clearing, restoration in the candidate panel and the need to click the existing **生成行程草案**. It does not query or automatically replace a place. Without rain, this is plainly the user's manual choice. Required places cannot be excluded here. With no usable weather evidence, this panel offers no weather adjustment action; ordinary candidate selection remains available
8. The existing first-three candidate rule remains: excluding one POI can promote the old fourth candidate, but it is not an automatically indoor substitute. A new draft needs a new check; required order/confirmed requirements remain intact, while new route queries may change times/results. Empty/no-alternative/failed regeneration follows existing validation, never fabricated POIs or restored stale drafts
9. A failed weather refresh can retain the old full batch but never reactivate its check. A new explicit check of a still-fresh prior batch carries **基于上次成功查询**, original query/publication times and the refresh failure. Map/Marker/Mock day and map traffic mode remain independent in both directions

### Delivery manifest — 13 files

1. `docs/PROGRESS.md`
2. `frontend/src/components/trip-plan-result.tsx`
3. `frontend/src/components/trip/weather-forecast.tsx`
4. `frontend/src/components/trip/weather-schedule-check.tsx` (new)
5. `frontend/src/components/trip/weather-schedule-check.integration.test.tsx` (new)
6. `frontend/src/lib/use-schedule-preview.ts`
7. `frontend/src/lib/use-weather-forecast.ts`
8. `frontend/src/lib/use-weather-display-clock.ts` (new; extracted shared local clock)
9. `frontend/src/lib/use-weather-schedule-check.ts` (new)
10. `frontend/src/lib/use-weather-schedule-check.test.tsx` (new)
11. `frontend/src/lib/weather-schedule-check.ts` (new)
12. `frontend/src/lib/weather-schedule-check.test.ts` (new)
13. `frontend/src/lib/weather-schedule-check.test-fixtures.ts` (new; fictional offline fixtures)

### Automated checks actually run this round

| Command (frontend unless stated) | Actual result |
| --- | --- |
| `npm test` | **770 passed**, 29 files; final full run 2026-10-10 13:24 Shanghai time |
| `npm run lint` | Passed |
| `npm run typecheck` | Passed (`next typegen && tsc --noEmit`) |
| `npm run build -- --webpack` | Passed, production static generation |
| `git diff --check` (root) | Passed; staged check repeated at closeout |
| Full backend suite | **Not rerun**: no backend changes. Historical backend results below are not this round's runs |

- 41 new tests (16 pure-rule, 20 hook/state, 5 result-composition tests) cover real-draft IDs/dates and same-name distinct IDs, missing/unknown/stale/past/partial-period evidence, day/night separation, user-only exposure, non-mutation and explicit clock rules
- Controlled tests cover explicit exclusion/regeneration, candidate promotion/restore/all-optional exclusion without required, failed regeneration, same-ID setting retention/new-ID unknown, required exclusion refusal, replacement-report identity, synchronous same-event rejection, input ABA, old success/error/finally during newer work, edit/unmount, midnight/action-time recheck, visibility/focus and failed-refresh provenance. Existing weather, candidate, required/optional scheduling, walking/transit, binding and map tests remain in the full run
- These tests use fictional fixtures and mocked services/SDK; they are **not live rain, live upstream failure or live request-attempt measurements**. No production fake-weather switch or relaxed validation was introduced

### Real Chrome acceptance and stable evidence

- Chrome **155.0.8059.39**, actual page/service interaction on 2026-10-10 13:21–13:25 Asia/Shanghai. Production frontend at `http://localhost:3000`, existing backend `http://127.0.0.1:8000`. Real POI search confirmed 静安寺 (`B00154BDE9`, accommodation reference) and 武康大楼 (`B00155ITJ1`, required). Exact normalized request/response evidence is in the receipt; no upstream secret URLs/headers/HAR were exported
- Requested **2026-10-10**, 09:00–18:00, 摄影 interest. Real weather covered that date: **day 多云 26°C / night 晴 18°C**, source published **2026-10-10 13:01:17 +08:00**. Both periods triggered no rain/snow rule. This is **no-rain + explicit user adjustment acceptance**, not live rainy-weather acceptance
- Initial real walking draft: 静安寺 → 武康大楼 2,156s → 36min; required visit 09:36–10:36; then 延中广场公园 3,498s → 59min, wait 11:35–12:00, lunch 12:00–13:00, optional visit 13:00–14:00, return 2,618s → 44min, lodging return **14:44**. Status partial reflects only one daily optional slot, not failed required visits
- Explicitly excluding 延中广场公园 (`B0FFG188OL`) cleared the draft and marked that candidate excluded, with **no new request**. Manual regeneration retained required 武康大楼; optional 辅德里公园 (`B00155HNA3`) was visited 13:00–14:00, return 1,650s → 28min, lodging return **14:28**. Old fourth 人民公园-相亲角 entered the submitted first three. Required annotation remained; newly visited 辅德里 defaulted unknown. The old check stayed disabled until a new explicit check

All ten screenshots below were opened and visually inspected after saving. Evidence remains **outside Git** at [`/Users/zijing/projects/trippilot-evidence/5c-2/`](/Users/zijing/projects/trippilot-evidence/5c-2/).

| Actual observation | Result | Evidence |
| --- | --- | --- |
| Real required + optional walking draft, lunch and lodging return | Passed | [01 initial draft](/Users/zijing/projects/trippilot-evidence/5c-2/01-real-draft-before-check.png) |
| User labels and explicit check; no rain claim; local business request count stays 6→6 and draft identical | Passed | [02 check and expanded basis](/Users/zijing/projects/trippilot-evidence/5c-2/02-real-weather-check.png) |
| Explicit optional exclusion, candidate state synchronized, draft cleared; requests stay 6→6 | Passed | [03 exclusion / invalid report](/Users/zijing/projects/trippilot-evidence/5c-2/03-explicit-exclusion-invalidates.png), receipt |
| Only manual generation requests new routes; excluded POI absent, required retained, new optional unknown | Passed | [04 regenerated draft](/Users/zijing/projects/trippilot-evidence/5c-2/04-regenerated-draft.png), receipt |
| Annotation change invalidates only check (requests 7→7); weather refresh makes one weather request (7→8), preserves draft and leaves old check invalid | Passed | [05 invalidation](/Users/zijing/projects/trippilot-evidence/5c-2/05-weather-refresh-invalidates-only-check.png) |
| Actual complete date coverage, day/night weather and original publication time | Passed | [06 real forecast](/Users/zijing/projects/trippilot-evidence/5c-2/06-real-weather.png) |
| 400px browser viewport, document scrollWidth 400, readable single-column labels/report/actions | Passed | [07 narrow check](/Users/zijing/projects/trippilot-evidence/5c-2/07-check-400px.png) |
| Existing activities bound to 静安寺/武康大楼; real walking query 2,695m / 2,156s, base map/route visible; draft and check unchanged | Passed | [08 existing map route](/Users/zijing/projects/trippilot-evidence/5c-2/08-existing-map-route.png) |
| 外滩 activity → 静安寺 selected/centered Marker; previous Marker normal, route retained; no additional query | Passed | [09 activity → Marker](/Users/zijing/projects/trippilot-evidence/5c-2/09-activity-marker-selected.png) |
| 武康 Marker → 午餐与休息 activity selected/scrolled into view, Marker colors switch, no request/check/draft change | Passed after viewport-aligned recheck | [10 Marker → activity](/Users/zijing/projects/trippilot-evidence/5c-2/10-marker-selects-activity.png). First partially visible map click did not switch selection and is explicitly **not** counted as passing evidence; whole-map-in-viewport real pointer click then verified both states |

Sanitized receipt: [11 request counts, normalized results and action observations](/Users/zijing/projects/trippilot-evidence/5c-2/11-sanitized-receipt.json). Exactly **11 observed local business requests**: `/places/search` 4, `/trips/plan` 1, `/places/candidates` 1, `/trips/schedule-preview` 2, `/weather/forecast` 2, `/routes/walking` 1; all HTTP 200. Label/check/exclude/Marker operations did not add business requests. No console errors; one existing Canvas2D `willReadFrequently` performance suggestion. These counts do **not** include map tile traffic and do **not** establish individual upstream HTTP attempts

### Remaining limits and deliberate choices

- No unresolved 5C-2 business defect/core acceptance blocker observed. Actual rain/snow, stale/unknown/no-coverage checks, clock transitions, service failures and races are controlled-test coverage, not fabricated live evidence. 400px Chrome is not a physical phone/touch-device acceptance
- No weather-driven automatic exclusion, indoor-place inference, optimal/reliable travel guarantee, exact activity weather-hour matching, transit weather-impact assessment or in-place replacement. Weather inspection is explanatory; only the user's ordinary candidate exclusion changes the **next explicitly generated** draft
- Existing walking/transit references and query budgets remain unchanged: request-local throttling, shared 20-second search deadline (cancellation cleanup can add return time), 25-second browser timeout and bounded retries. No account-wide rate-limit claim. Historical real `data_error`, dedicated subway/transfer samples, broader live-fault and six-required/three-optional full-load limitations remain; no new real transit query was needed for this weather-only feature
- Webpack is the validated build path; the historical Turbopack/environment limitation is retained. No backend full-suite rerun, dependency/security maintenance, persistence or next-stage work is claimed
- Interview explanation: keep **facts** (validated draft/weather snapshots), **user intent** (exposure/exclusion) and **derived advice** (pure deterministic association) separate. A synchronous revision plus report identity and action-time clock check prevents stale advice from authorizing a mutation; reusing the existing candidate→schedule path avoids two competing exclusion states. Explicit actions and preserved uncertainty are favored over pretending to know POI indoor status or exact weather at a planned visit time

## Milestone 5C-1 Actual Implementation and Acceptance (2026-10-10)

### Local-only closeout (2026-10-10)

- User authorized a local commit after implementation and acceptance. Stage only the 17-file manifest below, excluding all five original Python caches, environment files and the external evidence directory. Commit message: `feat: add Shanghai weather forecasts and daily travel hints`
- Current source/test files have no modification timestamps after the recorded acceptance; their closeout SHA-256 snapshot is checked again against the staged contents. Only this documentation's current closeout status is edited. Staged manifest/diff, whitespace and credential checks are performed; the prior 286 backend / 729 frontend tests, lint, typecheck, Webpack build and real Chrome evidence are **not rerun or relabeled** as closeout testing
- All eight stable screenshots still match the receipt's hashes. `/Users/zijing/projects/trippilot-evidence/5c-1/` remains outside Git; no evidence is deleted or moved. The implementation-round baseline/handoff, source-time and cancellation limits, controlled-vs-real distinction, physical-phone and historical data-error/full-load/Turbopack limitations remain intact below. Forecast hints still do not participate in scheduling
- No push, amend or reset. Preserve the two preceding unpushed commits; this local closeout adds the third. Stop here

### Baseline and scope

- Actual workspace `/Users/zijing/projects/trippilot`, branch `main`, baseline `01224f166a5ff11057b4d522b6be0f4c579b877d`. Both unpushed local commits remain intact. No temporary project copy, dependency, configuration/credential/CORS/proxy change, commit or push
- An independent **上海逐日天气预报** panel appears immediately after confirmed requirements and before candidate preparation. Its dates come only from `plan.request.start_date/end_date`, not the old two-day Mock example or its day selector
- Weather is queried only by **查询天气 / 刷新天气**. Hints do not participate in scheduling, candidate filtering/exclusion, required order, budget, activity binding or map routes. The old Mock weather card remains clearly labeled in the old Mock section; replanning remains disabled

### Data flow, validation and explainable rules

1. The browser submits only `{start_date, end_date}` to `POST /weather/forecast`, with a strict inclusive 1–3-day date range and no client-selected city, upstream or credentials
2. The backend reuses server settings, `httpx`, safe API validation/errors and existing log protection. `AmapWeatherClient` makes one logical Shanghai batch query to the fixed HTTPS `v3/weather/weatherInfo` upstream, using `city=310000`, `extensions=all`, `output=JSON`, the existing Web Key and **no redirects**. No local HTTP self-call or daily fan-out
3. Conversion validates the batch and every returned cast before building a date lookup. Each requested date appears exactly once in request order, marked available/unavailable. Empty `forecasts` or `casts` is valid no coverage; broken structures, wrong city, invalid/duplicate dates and invalid non-empty scalar values fail safely, even when the bad cast is outside the requested dates. Missing dates are not called out-of-range based on min/max and are never filled from adjacent dates, old snapshots or Mock values
4. The independent response includes date echo, `source=amap`, 上海市/310000, Asia/Shanghai, successful `queried_at`, nullable original `reported_at`, publication status, freshness-at-query, complete/partial/none coverage, and separate day/night descriptions, temperatures, wind direction/power and rain/snow classification evidence. Temperatures remain **day/night**, not maximum/minimum. Null/empty strings/empty arrays normalize to unknown; 0 and negative temperatures remain valid. Non-finite/bool/invalid values fail; the defensive temperature bound is −100…100°C, not a vendor coverage promise. Text is bounded to 100 Unicode characters and rejects control characters/BOM
5. Official exact weather-description sets classify rain, snow, mixed rain/snow, known non-rain/snow or unknown (`amap_text_precipitation_v1`). No substring guessing: unfamiliar descriptions remain visible and unevaluated. Each period retains its original description as evidence. Night rain does not imply daytime impact; absence of a triggered rule is not a promise of suitable travel. No rain probability, hourly/POI-level weather, inferred indoor/outdoor POI classification or fabricated forecast
6. `reporttime` is interpreted in Asia/Shanghai and normalized with `+08:00`; query time is UTC. Missing, unparseable, pre-2000 or more-than-five-minutes-future source publication is unknown, never replaced by query time. Five minutes is a defensive clock tolerance. **More than 24 hours** since source publication is the product's stale rule, not AMap's guaranteed validity. Past dates show that old forecasts are not historical observations. Stale, unknown-publication and past-date values provide no current-validity travel advice
7. A local display clock updates at Shanghai midnight, the 24-hour boundary, at most a minute between display ticks, visibility restoration and focus. It never fetches. A new snapshot synchronously gets a current display clock, avoiding a stale previous tick briefly authorizing guidance

Official references checked on this implementation round: [AMap weather API](https://lbs.amap.com/api/webservice/guide/api-advanced/weatherinfo), [weather-description code table](https://lbs.amap.com/api/webservice/guide/tools/weather-code/), and [AMap Shanghai district example using 310000](https://lbs.amap.com/api/amap-ui/reference-amap-ui/geo/district-cluster). The real diagnostic response also matched 上海市/310000. The documented short forecast horizon is not substituted for actual date matching

### Request budget and frontend isolation

- One logical weather query allows **at most two upstream HTTP attempts**, retrying once only for connection failure/connection timeout. Business errors, rate limits, HTTP/redirect failures, invalid payloads and read timeouts are not retried. Both attempts share one **10-second search deadline**, including parsing checks; cancellation finalization/client cleanup may add return time. Frontend timeout is 15 seconds; no frontend retry, polling, cache service or new global framework
- Safe statuses follow existing conventions: 422 invalid input, 503 missing configuration, 504 timeout, 502 upstream/business/data error. The browser shows fixed safe messages, not upstream exception bodies or credential-bearing URLs
- `useWeatherForecast` owns pending/version/controller and the last successful **whole-batch** snapshot. Refreshing and refresh failure may show that same old snapshot with its original timestamps and an explicit previous-result notice. A successful new batch fully replaces it even with zero coverage; previous-result status is separate from source staleness
- Input changes clear displayed state in a conditional same-component render adjustment and update input/abort/version in the synchronous layout commit, before later events. `editRequest` calls `weather.invalidate()` before returning to the form. Unmount cleanup aborts; stable query reads current input. Success, error and finally are version-guarded, including A→B→A and an older query closure. No passive-effect-only reset, persistence or shared candidate/schedule/map state
- Cross-language review additionally aligned hidden BOM rejection and microsecond freshness comparisons; controlled regression now covers receipt of a newly stale response before the preceding display timer ticks

### Delivery manifest — 17 files

1. `backend/app/schemas/weather.py` — independent strict weather request/response and exact classification rules
2. `backend/app/integrations/amap_weather.py` — fixed Shanghai batch, normalization, publication handling, bounded attempts/deadline
3. `backend/app/api/weather.py` — safe independent weather endpoint and existing settings/client dependencies
4. `backend/main.py` — weather router registration only
5. `backend/tests/test_weather.py` — offline API/conversion/security/deadline/cancellation coverage
6. `backend/tests/export_weather_contract.py` — deterministic Python normalized outputs, no config or HTTP access
7. `frontend/src/types/weather.ts` — independent types, strict runtime echo/coverage/rule validation and Shanghai freshness helpers
8. `frontend/src/types/weather-contract.test.ts` — actual Python outputs → actual TypeScript guard and mutation rejection
9. `frontend/src/lib/weather-api.ts` — explicit fetch, 15-second timeout and safe errors
10. `frontend/src/lib/weather-api.test.ts` — request/response contract, date/freshness/field rules, timeout/cancel/security tests
11. `frontend/src/lib/use-weather-forecast.ts` — independent snapshot lifecycle and stale-request isolation
12. `frontend/src/lib/use-weather-forecast.test.tsx` — explicit query/refresh, ABA, old success/error/finally, edit/unmount tests
13. `frontend/src/components/trip/weather-forecast.tsx` — panel, day/night fields, bounded guidance and network-free display clock
14. `frontend/src/components/trip/weather-forecast.test.tsx` — coverage, unknown values, source time, midnight/24h/new-snapshot and display tests
15. `frontend/src/components/trip/weather-forecast.integration.test.tsx` — real parent-state isolation with offline services/SDK, date source and edit lifecycle
16. `frontend/src/components/trip-plan-result.tsx` — independent hook/panel integration and synchronous edit invalidation
17. `docs/PROGRESS.md` — current implementation/acceptance evidence, retaining historical records

### Automated verification — final frozen version, 2026-10-10

| Command / check | Actual result |
| --- | --- |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -q` | **286 passed** (262 existing + 24 weather). No live AMap requests. Existing Starlette/httpx TestClient deprecation warning remains; no dependency change |
| `cd frontend && npm test` | **729 passed, 26 files**, including prior candidate, scheduling, binding and walking/transit/map regressions |
| Python → actual TypeScript contract test (included above) | **348 controlled Python outputs accepted; 2,082 semantic mutations rejected**. Exact description classes, partial/empty coverage, 1/2/3-day and cross-year dates, 0/negative/missing values, valid/stale/invalid report times and microsecond boundary. Uses backend `.venv/bin/python` when present, otherwise installed `python3` with project backend dependencies |
| `cd frontend && npm run lint` | Passed |
| `cd frontend && npm run typecheck` | Passed |
| `cd frontend && npm run build -- --webpack` | Passed; final build used in browser. Turbopack limitation remains, not retested/fixed |
| `git diff --check` plus new-file whitespace check | Passed |
| Final scope/security audit | Exactly the 17 listed delivery files; no staged files. Configured credential values were checked against delivery text and the sanitized receipt without printing them; no match. SHA-256 of all five original caches and the complete prior acceptance-history suffix remained unchanged |

Coverage includes malformed/duplicate/unordered dates, complete/partial/zero coverage, unrelated corrupt casts, unknown weather, source-time ambiguity, Shanghai midnight, strict day/night guidance, refresh failure preservation and success-without-coverage replacement, safe errors, two-attempt/shared-deadline/cancellation behavior, old success/error/finally and module independence. Controlled fixtures are never represented as live weather or upstream fault acceptance

### Real Chrome acceptance — 2026-10-10, approximately 01:20–01:25 Asia/Shanghai

- Used real Chrome **155.0.8059.39**, existing ports 3000/8000, restarting only the inspected previously agent-started services to load the final implementation. Frontend used the final Webpack production build; backend ran with `-B` and no access log. No Mock interception, response modification or fake map/weather data
- Confirmed actual POIs: 住宿参考点 **静安寺** and 必去 **武康大楼**, photography interest, October10–12, 08:00–18:00. The fixed old Mock has two dates; the independent weather panel correctly displayed all three request dates
- Browser observed **three explicit POST /weather/forecast requests, all HTTP200**: first query, same-date refresh, then distant-date query. Entering results, old day switching, candidate fetch/exclude/restore, draft generation/mode toggling and Marker operations did not add weather requests. An earlier separate permission/field diagnostic made **one directly observed upstream attempt** (HTTP200); its casts contained October10–13. Browser requests do **not** prove the server's individual upstream-attempt count; the ≤2/query bound is code/controlled-test evidence
- Live publication was **2026-10-10 01:01:13+08:00**, preserved across refresh. October10 day/night: 多云/晴,26/18°C; October11: 多云/多云,25/18°C; October12: 阴/多云,26/19°C. Wind power `1-3` remained source text. These observations apply to this captured batch, not a permanent forecast

All **eight PNGs were opened and visually checked after saving**, including recapturing an ambiguous intermediate map image as a complete viewport. Stable directory: [/Users/zijing/projects/trippilot-evidence/5c-1/](/Users/zijing/projects/trippilot-evidence/5c-1/). [09-safe-acceptance-record.json](/Users/zijing/projects/trippilot-evidence/5c-1/09-safe-acceptance-record.json) contains normalized weather snapshots, only safe local paths/statuses/timestamps, counts, UI checks, screenshot dimensions/SHA-256 and limitations; no keys, headers, SDK URLs, raw upstream payloads or HAR logs

| Test | Actual observation | Evidence |
| --- | --- | --- |
| Explicit trigger / all three requested dates | Passed. Panel started unqueried with October10/11/12; zero weather requests on entry | [01 unqueried](/Users/zijing/projects/trippilot-evidence/5c-1/01-idle-three-dates.png) |
| Real forecast with source/publication and day/night semantics | Passed. HTTP200, complete coverage, original published time and separate day/night temperature/wind. No rain probability or current-suitability promise | [02 three-date forecast](/Users/zijing/projects/trippilot-evidence/5c-1/02-real-weather-three-dates.png) |
| Weather refresh preserves existing result modules | Passed. Candidate text, generated draft and entire old Mock/map text were equal before/after refresh; only one extra weather request and no candidate/schedule/route query. Existing route polyline remained visible | [03 map before](/Users/zijing/projects/trippilot-evidence/5c-1/03-bound-map-before-weather-refresh.png), [04 map after](/Users/zijing/projects/trippilot-evidence/5c-1/04-bound-map-after-weather-refresh.png), [05 unchanged draft](/Users/zijing/projects/trippilot-evidence/5c-1/05-preserved-draft.png) |
| Existing main-flow/Marker regression | Passed. Real searches and explicit bindings for two adjacent activities; walking query2695m/2156s and visible blue geometry. Activity→静安寺 Marker highlighted/centered; clicked武康大楼 Marker highlighted it and selected/scrolled its activity, first Marker returned normal. No extra weather query | [06 selected Marker](/Users/zijing/projects/trippilot-evidence/5c-1/06-activity-marker-after-refresh.png), [04 selected second activity](/Users/zijing/projects/trippilot-evidence/5c-1/04-bound-map-after-weather-refresh.png) |
| Candidate/draft regression | Passed. Real candidate pool and a complete three-date walking draft. Day1 required08:36–09:36, optional延中广场10:35–11:35, waiting then lunch12:00–13:00, return13:44; subsequent dates returned09:57/09:56. Weather did not alter this input or output | [05 timeline](/Users/zijing/projects/trippilot-evidence/5c-1/05-preserved-draft.png), sanitized receipt |
| 400px layout | Passed in desktop Chrome viewport; document width400, panel360, no horizontal overflow; all three cards and query button visible in scrollable panel | [07 narrow forecast](/Users/zijing/projects/trippilot-evidence/5c-1/07-weather-400px.png) |
| Distant future including year boundary | Passed. December30/31,2026 and January1,2027: HTTP200, `coverage=none`, all day/night values null. Explicitly missing, not request failure or invented out-of-range assertion; no recent values substituted | [08 no coverage](/Users/zijing/projects/trippilot-evidence/5c-1/08-future-no-coverage.png) |
| Edit/regenerate/refresh lifecycle | Passed. Edit retained confirmed POIs, regenerated dates started weather idle; browser reload returned empty form and did not issue another weather query (count stayed3) | Sanitized receipt |

Console: no observed page errors or console errors; three AMap canvas `willReadFrequently` performance warnings (URLs not exported). Observed scoped local business responses were HTTP200. This is not a claim that every SDK/tile network resource or server-side upstream attempt was individually audited

### Limits and handoff

- Live weather was cloudy/sunny/overcast, not a live rain/snow, stale-publication or outage sample. Rain/snow separation, unknown/old/past dates, midnight, failed refresh, deadline/cleanup and races are controlled-test results only. Actual no-coverage and successful refresh were observed. Weather permission was available; no blocking key/connection issue remained
- Desktop 400px is not physical-phone/touch acceptance. Dedicated live subway/transfer operations, six-required-plus-three-optional live full-load, broader real upstream failures and Turbopack remain unverified here. The historical Oriental Pearl `data_error` limitation and prior scheduling batch-local throttling/20-second-search-vs-cleanup limits are retained below, not resolved by weather work
- Forecast presence is not accuracy, historical actual weather or operational suitability. The conservative design separates data retrieval/normalization, publication freshness and transparent text-rule hints; none authorizes automatic replanning. In an interview, explain whole-batch atomic replacement, calendar-date matching rather than array position, source-time freshness rather than query-time laundering, and abort **plus** version checks for stale success/error/finally
- Stop at 5C-1. No commit/push; two local commits and all five original Python cache contents remain intact. Stable evidence is outside the repository and is not deleted or moved into temporary storage

## Milestone 5B-3 Actual Implementation and Acceptance (2026-10-09)

### Evidence accessibility follow-up — new minimal recheck (2026-10-09)

- **Original evidence restoration: not possible in the accessible locations checked.** `/private/tmp/trippilot-5b3-evidence.wkQHFf` no longer existed. Related filename/directory searches in project directories, Codex outputs, `/private/tmp`, the user's temporary directory and relevant Downloads/Desktop/Documents locations found no migrated screenshots or sanitized JSON. No unrelated/private content was read or access restrictions bypassed. The original 18 screenshots and receipt filenames below describe the earlier run; they are **not currently accessible evidence**, and none was reconstructed or relabeled as recovered
- **This follow-up is a fresh, deliberately limited real Chrome acceptance**, October 9, 2026 around 23:42–23:44 Asia/Shanghai, Chrome155.0.8059.39. Both local ports were initially unused; started the existing FastAPI service with `-B`/no access log and existing Webpack production build on8000/3000. No rebuild, source change, parser relaxation, Mock replacement, walking fallback or broader acceptance run. Baseline stays local `16e68ff`, no commit/push
- Stable evidence directory: [/Users/zijing/projects/trippilot-evidence/5b-3/](/Users/zijing/projects/trippilot-evidence/5b-3/). **All nine PNGs were opened and visually checked** for complete target content. [10-safe-acceptance-record.json](/Users/zijing/projects/trippilot-evidence/5b-3/10-safe-acceptance-record.json) contains the new normalized requests/results, safe paths/statuses, observed counts, screenshot dimensions/SHA-256 and explicit scope/limits; no credentials, headers, SDK URLs, raw upstream responses or network logs. Images are original page captures; transit geometry in the JSON is summarized by counts
- New sample: confirmed accommodation静安寺, required武康大楼, one day October10 08:00–18:00, photography→parks; explicitly fetched the real candidate pool and selected transit for generation. First three eligible optional POIs remained延中广场公园、辅德里公园、静安雕塑公园. Query results below are this run's values, **not reused from the original acceptance**

| Minimal recheck | Result / actual observation | Stable evidence |
| --- | --- | --- |
| Real transit required + optional, lunch and return | Passed. Required **08:55–09:55**, optional延中广场 **10:47–11:47**, wait11:47–12:00, lunch12:00–13:00, accommodation return **13:47**. Adopted total estimates **3262s→55min / 3092s→52min / 2786s→47min**; all eight logical edges valid. Response `partial` reflects two optional entries left as `day_slot_used`, not failed required visits | [01 full timeline](/Users/zijing/projects/trippilot-evidence/5b-3/01-transit-timeline.png) |
| Actual lines and stations | Passed. **76路** 静安寺→番禺路新华路, **320路** 天平路淮海中路→淮海中路嵩山路, **311路** 延安东路重庆北路→延安西路华山路. Access walking **1166/496/933m** respectively; each reference fare¥2. Total duration counted once; route-step durations are explanatory | [02 outward](/Users/zijing/projects/trippilot-evidence/5b-3/02-transit-leg.png), [03 optional approach](/Users/zijing/projects/trippilot-evidence/5b-3/03-transit-leg.png), [04 return](/Users/zijing/projects/trippilot-evidence/5b-3/04-transit-leg.png) |
| Optional stays480, baseline preserved | Passed. Rule change immediately removed the old draft, request count **1→1** until explicit generate. Required stayed **08:55–09:55**, with direct return **09:55–10:55** and all three optional outcomes `time_window`. First response's unused baseline-return edge3585s→60min and second response's3590s→60min both support10:55; not a claim that independent live query seconds never change | [07 required/direct return](/Users/zijing/projects/trippilot-evidence/5b-3/07-required-return-preserved.png), [08 reasons](/Users/zijing/projects/trippilot-evidence/5b-3/08-optional-time-window.png) |
| Mode switch clears without requesting | Passed. Switched successful transit draft to walking; timeline immediately absent, request count **2 before / 2 immediately after / 2 after two seconds**. Candidate/stay settings retained. No walking query was performed for this check | [09 cleared draft](/Users/zijing/projects/trippilot-evidence/5b-3/09-mode-switch-cleared.png), safe JSON |
| 400px layout | Passed, browser simulation. Viewport400×820/document width400, mode controls and expanded transit steps wrap without horizontal overflow. Target-element screenshots include their full content; longer steps can scroll vertically | [05 controls](/Users/zijing/projects/trippilot-evidence/5b-3/05-narrow-mode-controls.png), [06 full transit item](/Users/zijing/projects/trippilot-evidence/5b-3/06-narrow-transit-details.png) |

- Observed **six local business requests**, all HTTP200: 2 POI searches, 1 Mock plan, 1 candidate batch, 2 transit schedule previews. No extra route or schedule requests from mode switching/viewport changes. This is **not an observed upstream attempt count**. No business defect or `data_error` occurred in these two new batches; the historical Oriental Pearl malformed-access-geometry limitation remains unresolved and was not retried
- This late-evening query returned different reference buses/estimates from the earlier run; it still does **not validate next-day 08:00 operation, waiting or transfers**. No new physical-phone, subway/transfer-operation, old-map/Marker, real-fault, full-load or Turbopack acceptance; retain the previous limits. The 20-second search cutoff plus cancellation cleanup and request-local throttling still apply
- **No full tests/lint/typecheck/build rerun:** there were no business source changes. The earlier automated figures below remain implementation-round results, not new follow-up runs. Only documentation diff/format, artifact readability, sanitization and unchanged source/cache checks were performed. Repository change for this follow-up: `docs/PROGRESS.md` only; nine PNGs plus one sanitized JSON are outside the repository in the stable directory. Services remain available for review

### Baseline, scope and data flow

- Read root/frontend AGENTS, PROJECT_CONTEXT, PROGRESS and installed Next.js client/server-component documentation. Worked directly in `/Users/zijing/projects/trippilot` on `main` at local `16e68ff8d02ef698bcf472c1395b455aaba1bc0b`, one commit ahead of the existing remote-tracking record. No temporary implementation checkout, dependency, environment/key, adapter/parser, SDK proxy or CORS changes; five original Python cache hashes preserved
- Extend the existing panel and `POST /trips/schedule-preview`: confirmed requirements + first three eligible candidates + stays/lunch + selected mode → explicit generation/full snapshot → mode-specific existing adapter → normalized edges → pure required baseline → bounded optional tails → strict TypeScript guard → independent daily timeline. No HTTP self-call, automatic mode comparison, walking fallback, transit departure-date/time argument or new draft-map functionality
- The existing [official AMap direction documentation](https://lbs.amap.com/api/webservice/guide/api/direction) was checked for total duration seconds and access-walking distance semantics. The existing fixed-Shanghai transit adapter and its first complete, supported, valid proposal/ride-alternative selection remain unchanged; no claim of fastest/optimal routing

### Contract, algorithm and budget

- `ScheduleRequest.transport_mode` accepts only `walking | transit`, defaulting to walking for legacy callers. The response request echo and every edge carry an explicit mode. Successful transit edges retain the existing normalized `TransitRoute`, selection rule, query time and source; their `distance_meters` remains null because access walking is not total transit mileage
- Use the transit scheme's total `duration_seconds` **once**, with `ceil(seconds / 60)` for both display and scheduling. Never add access/ride-leg durations again. Nullable fare/leg duration and genuinely missing geometry stay unknown; valid partial-geometry schemes remain schedulable. Same-place movement is local zero without a fake line, fare or upstream query
- Timeline uses `walk` or `transit` movement items consistently, including returns and used-edge flags. A complete transit scheme is an indivisible block around lunch, not invented per-leg departure/arrival clocks. Pure scheduling functions do no I/O and reject cross-mode edges/baselines
- Preserve 1–3 actual dates, required original order, complete daily returns, stay provenance, lunch/wait rules and same-batch immutable required visits. Optional failure tries subsequent candidates or keeps the original day; required failure retains the verified prefix/return. Statuses distinguish `no_route`, `unsupported`, `timeout`, `data_error`, `failed` and never-started `budget_exhausted`; unsupported required routes have a distinct reason
- Only the selected `AmapWalkingClient` or `AmapTransitClient` is called. Request-local directed memoization includes mode, endpoint IDs and normalized coordinates. Required and optional phases share the original **20-second search deadline**, **≤3 concurrency** and **≥0.4-second logical-start spacing**. Bounds remain 17 logical edges / 34 attempts without optional inputs and 29 / 58 with them; only the existing connection-failure retry is used
- Queue/throttle time consumes the same deadline. Cancellation is drained and can add return time beyond 20 seconds; frontend timeout remains 25 seconds. Pacing is local to this generation, not account-level rate limiting. No cross-request cache or new retry layer

### Frontend lifecycle and presentation

- `use-schedule-preview.ts` keeps mode in draft settings and the synchronous settings ref. Changing mode immediately aborts/increments the version/clears the preview before updating React state; query reads the latest ref even when an old closure runs in the same event. Old success, error and finally cannot mutate a newer request, including walking→transit→walking
- Switching mode preserves candidates, exclusions, stays and lunch and does not request. Candidate refresh/exclude/restore and rule changes keep the existing synchronous invalidation chain. Edit/regenerate/unmount clear the draft; new result/refresh restores walking default. The legacy map's `routeMode` remains entirely separate
- Neutral “行程草案” controls support both modes. Transit items show total seconds/ceiling minutes, separate access distance, nullable reference fare and actual ordered line/station steps, without multiplying by travelers or changing the Mock budget. The notice explicitly says: “采用本次查询的参考方案估时，尚未验证旅行日期及草案出发时刻的运营班次、等车和换乘可行性。”
- Runtime validation checks full snapshot/mode echo, every edge's mode/data shape, total-to-minute conversion, transit details, valid status/reason references, movement/return/lunch continuity, optional identity/order and budget bounds. Reuse the existing transit response guard; do not weaken malformed-geometry or unsupported-leg handling

### Automated checks — controlled, not real service acceptance

| Command/check | Result in this implementation round |
| --- | --- |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -q` | **262/262 passed**: 240 existing + 22 new transit-schedule tests |
| `cd frontend && npm run test` | **21 files / 622 tests passed**, including the final test-only additions |
| `cd frontend && npm run lint` | Passed, no warnings |
| `cd frontend && npm run typecheck` | Passed: Next type generation and TypeScript |
| `cd frontend && npm run build -- --webpack` | Passed; this production build, newer than all changed frontend business sources, was used in Chrome. A later test-only assertion update does not affect the served bundle |
| Python outputs → actual TypeScript response guard | **300/300 controlled outputs accepted; 1,494/1,494 invalid mutations rejected** |
| `git diff --check`, new-file whitespace and secret/content audit | Passed; no credentials/environment files included and all five original cache hashes unchanged |

- New coverage includes omitted-mode compatibility; only-selected-client calls/no fallback; transit total counted once with unknown leg durations/fare and partial geometry; same-place zero; unsupported vs empty/error/deadline; required-first, optional fallback, lunch/return; both modes' 17/34 and 29/58 bounds, shared pacing/deadline/cancellation; mode ABA and stale success/error/finally; same-event old query closure; candidates and map-mode isolation
- Historical cross-language scripts `cross-language-cases.py` and `validate-cases.cjs` used 150 deterministic cases per mode and the actual compiled frontend guard, without HTTP. They covered dates, lunch, zero movement, optional outcomes and failures; mutations included mode/echo/ceiling mismatches, double-counted totals, access distance misrepresented as total, negative fares and missing stations. The recorded command was `PYTHONPATH=. .venv/bin/python -B <evidence>/cross-language-cases.py | node <evidence>/validate-cases.cjs` from backend. These temporary scripts were also not recovered in the evidence follow-up; no rerun or recreation is claimed
- Existing Starlette/httpx deprecation remains. Backend has no separate configured lint/typecheck script. No tests use real AMap quota. Historical Turbopack restrictions were not retried or claimed fixed; no dependency advisory remediation was included

### Real Chrome acceptance — actual bus reference schemes

- Actual Chrome **155.0.8059.39**, frontend `localhost:3000`, backend `127.0.0.1:8000`. Verified ports were unused before starting project FastAPI and the final Webpack production server; startup was clean. Services remain running for review. No Mock POI, route adapter or map replacement was used
- Initial one-day request: October 10, 2026, 08:00–18:00, accommodation **静安寺**, required **东方明珠广播电视塔**, photography interest. Later explicitly replaced the required POI with **武康大楼** and regenerated, then fetched real park candidates: **延中广场公园 / 辅德里公园 / 静安雕塑公园** as the first three
- **Historical evidence location, now unavailable:** `/private/tmp/trippilot-5b3-evidence.wkQHFf/`. The implementation-round record says all18 screenshots were opened and visually checked, and `19-safe-request-evidence.json` held normalized local business receipts without credentials/headers/SDK URLs/raw logs. These original files were not recovered; filenames in the historical table below are not current links. For accessible **new minimal recheck** evidence use [/Users/zijing/projects/trippilot-evidence/5b-3/](/Users/zijing/projects/trippilot-evidence/5b-3/) and the follow-up table above. Browser tool logs are not delivery evidence

| Item | Result | Actual observation / evidence |
| --- | --- | --- |
| Walking baseline and explicit mode switch | Passed | 静安寺→东方明珠: **10310s→172min**, visit **10:52–11:52**, lunch then return **10311s→172min**, arrival **15:52**. Switching to transit cleared the preview, count **1→1** until explicit generation. `01-walking-preview.png`, `02-mode-switch-clears.png` |
| Real transit roundtrip and details | Passed | Outbound **4270s→72min**, access **2144m**, fare **¥2**, **01路**, 延安西路华山路→世纪大道浦东南路. Visit **09:12–10:12**; return total **4359s→73min**, access **1345m**, fare **¥4**, arrival **11:25**. Total is not increased by access legs. `03-transit-roundtrip.png`, `04-transit-line-stops.png` |
| Actual upstream malformed geometry | Safe failure observed, sample limited | A later explicit candidate-inclusive attempt and one explicit retry returned required outbound `data_error`; no optional phase and no walking fallback. `05-live-data-error-preserved.png`. A separate bounded diagnostic found an upstream-success scheme rejected by the existing adapter for undrawable/degenerate access-walking geometry. Parser/security rules were not relaxed; these attempts are **not successful schedule acceptance** |
| Transit optional tail and lunch | Passed with new real sample | 武康 visit **08:44–09:44**, **2613s→44min** via **76路**, 静安寺→番禺路新华路; added 延中广场 **10:23–11:23**, **2327s→39min** via **911路**, 淮海中路武康路→淮海中路嵩山路. Wait to12:00, lunch12:00–13:00, return via **37路**, **2533s→43min**, arrival **13:43**. Access distances/fare matched response; 8 edges, 7 valid/1 unused data error. `06-transit-optional-timeline.png`, `07-optional-outcomes.png` |
| Required baseline and optional rejection | Passed | Changing all three optional stays to480 immediately cleared preview, count **5→5** before generate. Next result kept 武康 **08:44–09:44**, returned **10:31** (2795s→47min); all three optional outcomes were dated `time_window`. Previous required-only timing also follows the same-batch outbound edge+60min stay. `08-required-preserved-480.png`, `09-optional-too-long.png` |
| 400px layout | Passed, simulated | Viewport400×820/document width400, wrapped controls, names and transit details without horizontal overflow. Not physical phone/touch acceptance. `10-narrow-settings.png`, `11-narrow-transit-steps.png` |
| Exclude/restore, switch back and regenerate | Passed | Exclusion/restoration cleared draft, count **6→6→6**, no new route calls. Restored60min stays, switched to walking with no request, explicitly generated: 武康 **08:36–09:36**, 延中广场 **10:35–11:35**, return **13:44**; 2156s→36min, 3498s→59min, 2618s→44min. `12-back-to-walking.png`, safe JSON |
| Legacy walking/transit map independent | Passed | Separately bound Mock activities to 静安寺/武康. One walking request returned **2695m/2156s**; one transit request returned **2609s**, access1166m/fare¥2 and76路 steps. Draft text stayed unchanged and preview count7. Screens13/14 are partial viewport captures, **not complete-map evidence**; `17-draft-switch-keeps-old-map.png` and `18-full-transit-map.png` show the full real segmented bus/access route, both markers, labels and attribution |
| Activity ↔ Marker, no extra queries | Passed | “在地图查看” for 外滩漫步 centered/red-highlighted Marker1 (offset0,0), Marker2 normal. Clicking Marker2 focused 午餐与休息 and swapped highlights. Transit line/result and walking draft retained; counts remained7 schedule/1 walking/1 transit. `15-activity-marker-centered.png`, `16-marker-activity.png` |
| Reverse mode isolation | Passed | Draft walking→transit→walking cleared only draft, kept all stays60 and candidate state; legacy map remained transit with its same result and line. No extra requests; full-route control still worked. `17-draft-switch-keeps-old-map.png`, `18-full-transit-map.png`, safe JSON |

- Observed local business requests: **7 POI GETs, 2 Mock plan POSTs, 2 candidate POSTs, 7 preview POSTs, 1 legacy walking POST and 1 legacy transit POST**, all HTTP200. HTTP200 includes the two safely unscheduled data-error previews; it is not a claim every edge succeeded. These are not individually observed upstream HTTP attempt counts. One separate successful read-only diagnostic was outside browser counts; its preceding sandbox attempt failed to connect
- Actual successful ride samples were **bus**, not dedicated subway acceptance. One Oriental Pearl return contained two bus lines, but this is not verification of real transfer feasibility/operations on the travel date. Console showed **0 errors / 2 warnings**, not claimed resolved. A few initial automation selectors timed out; corrected using actual accessible names, and incomplete screenshots were not counted as full-map evidence

### Remaining limits and handoff

- Core implementation and focused live acceptance completed. The Oriental Pearl repeated malformed access-geometry sample remains rejected by the unchanged adapter; other valid live samples passed. A later unused optional edge also had a safely isolated data error, not independently diagnosed. Do not generalize the one diagnostic to all upstream errors or silently turn malformed geometry into missing geometry
- Controlled tests cover no-route, unsupported required legs, timeouts, retries, deadline cancellation and races; they are not fabricated real-fault demonstrations. No dedicated real subway/transfer-operation, physical-phone/touch, six-required-plus-three-optional full-load or account-wide quota acceptance was added
- Search cutoff remains **20 seconds plus cancellation cleanup**, and pacing is **request-local**; live browser counts do not establish all upstream attempts. Turbopack/font/dependency limitations remain as recorded historically
- Transit is this query's reference scheme, not travel-date departure/arrival validation or a guarantee of waiting/transfer feasibility. Required visit invariance is within the same request/mode/batch, not between independently queried modes. No opening/booking, total budget, optimized order, new map, real weather, LLM, persistence or subsequent milestone
- No staging, commit or push. Local baseline remains `16e68ff`; existing cache bytes unchanged. Browser-generated logs are outside the working tree

### Actual changed files (17; excludes five preserved caches)

- Backend modified (6): `backend/app/api/schedule.py`, `backend/app/schemas/schedule.py`, `backend/app/services/schedule.py`, `backend/app/services/schedule_routes.py`, `backend/tests/test_schedule.py`, `backend/tests/test_schedule_optional.py`
- Backend added (1): `backend/tests/test_schedule_transit.py`
- Frontend contract/state/tests modified (5): `frontend/src/types/schedule.ts`, `frontend/src/lib/schedule-api.ts`, `frontend/src/lib/schedule-api.test.ts`, `frontend/src/lib/use-schedule-preview.ts`, `frontend/src/lib/use-schedule-preview.test.tsx`
- Frontend UI/integration/tests modified (4): `frontend/src/components/trip/schedule-preview.tsx`, `frontend/src/components/trip/schedule-preview.test.tsx`, `frontend/src/components/trip-plan-result.tsx`, `frontend/src/components/trip/candidate-preparation.tsx`
- Documentation modified (1): `docs/PROGRESS.md`

## Milestone 5B-2 Actual Implementation and Acceptance (2026-10-06)

### Baseline, scope and contract

- Read applicable root/frontend AGENTS, PROJECT_CONTEXT, PROGRESS and local Next.js client-component instructions. Worked directly in `/Users/zijing/projects/trippilot` on `main` at `e917000c140e3f9fedb3a3b9adaa17b03fb75244`. Initial workspace contained only the five original Python caches; their hashes remain unchanged. No temporary implementation copy, new dependency, environment/key, proxy, CORS or old TripPlan changes
- Extend the existing `POST /trips/schedule-preview`, not a duplicate product module. Current confirmed requirements + valid candidate pool → filter optional role, excluded/required/accommodation/duplicate IDs → first three in pool order → separate stay settings → explicit generation → required baseline → optional tail trials → runtime-validated daily preview
- Request adds `optional_places`, default `[]` for legacy required-only callers. Required count 0–6, optional count 0–3, combined count at least one; reject duplicate/cross-role/accommodation-optional IDs and invalid strict Place data. Stay settings match the exact combined ID set, integer 15–480 minutes with independent default60/user provenance. Original 1–3 dates, daily window and lunch validation remain
- Every generation clones the full request snapshot. Response echoes it and adds `optional_results` in input order: place ID, nullable scheduled date, nullable `must_incomplete` reason, and dated attempts (`scheduled`, `time_window`, `no_route`, `timeout`, `data_error`, `failed`, `budget_exhausted`, `day_slot_used`). Existing `unscheduled` remains required-only; overall complete/partial/unscheduled considers all requested visits, while the UI separately counts required and optional visits
- Places retain only real POI fields, without fabricated prices, hours, visit durations or recommendations. Required and optional stays default to an explicitly editable planning setting. The panel lists trial names/count and the remaining out-of-scope count; outside the first three does not mean infeasible. Optional-only previews do not display a meaningless required-completion heading

### Required-first, immutable-prefix tail strategy

- Pure `build_required_schedule` retains 5B-1's fixed-order, lunch-aware daily-return rules. No required POIs creates empty requested dates and skips required route collection; both input lists empty is rejected before collection
- If any required visit is unscheduled, return its original verified baseline and reasons, mark every optional `must_incomplete`, and do not query optional edges. Otherwise process dates ascending, trying not-yet-scheduled optionals in input order; the first fit fills that day's single optional slot. Later optionals get `day_slot_used`, not a time-failure claim, and can be tried on following days
- On a nonempty baseline day, keep every item through the last required visit's end. Remove only the old return suffix, including wait/lunch caused solely by that return; simulate fixed-tail→optional, stay, optional→accommodation. Empty days start from accommodation at daily start. Prefix lunch is not duplicated, ceil(seconds/60) remains the calculation/display unit, and a full return must fit before committing
- Time/route failure tries the next optional without harming the baseline. If none fits, retain the original day exactly. Optional visits never repeat across dates, never move required dates/order/visit clocks, and never fill empty days with Mock activities. One per day is a product limit, not an optimality or exhaustive feasibility claim
- Dated outcomes distinguish actual time failure from route failure, requests that timed out after starting, deadline-expired edges never started, and a slot already taken. The UI does not declare untried dates globally infeasible. The rule/unknown section still disclaims opening/booking feasibility, budget, meals/detours and future-date walking conditions

### Shared directed route budget

- Reuse `AmapWalkingClient` directly. Required phase has only accommodation↔required and originally adjacent forward required edges, at most 17. Once that baseline is complete, prefetch only its fixed daily tails→optionals and optionals→accommodation, at most 12 new edges. No optional-to-optional matrix and no reverse-route substitution
- One `ScheduleRouteCollector` owns memoization by endpoint ID plus normalized six-decimal coordinates, semaphore≤3, monotonic start pacing≥0.4s, and the same absolute 20-second deadline for both phases. Phase two does not reset the deadline or pacer. Same-place edges are explicit zero movement with no upstream call; no cross-request cache
- At most 29 logical edges / 58 upstream attempts when optional inputs exist; required-only retains 17/34. The existing connection-only single retry is unchanged, with no batch/frontend retry. Controlled MockTransport tests exercised all 29 distinct edges with first-connect-failure/second-success, observing 58 attempts; that is automated evidence, not a live attempt count
- Search deadline includes queue/pacing time. No new nontrivial logical start occurs after expiry; pending tasks are cancelled and drained. Started work becomes timeout; never-started work becomes `budget_exhausted` with an explicit no-query message and no fabricated estimate. Cancellation finalization can add response time beyond 20 seconds; frontend timeout remains 25 seconds
- Pacing is local to one generation, not an account/global quota guarantee; other requests and the adapter's connection retry still consume quota. Known unused edge failures do not invalidate an established baseline. Prefetch is a bounded simplicity tradeoff: some candidate edges may be acquired but not adopted when the daily slot fills

### Frontend state and compatibility

- Reuse the candidate panel and existing preview. `useCandidatePool` publishes a synchronous snapshot before refresh, exclusion/restoration, result replacement and reset; `useSchedulePreview.updateCandidates` immediately aborts, increments its version and clears the draft. This is not effect-only invalidation, including actions in one React batch and A→B→A changes
- Candidate refresh disables generation. Partial successful candidates are usable with an incomplete-source notice. Full refresh failure retains the last valid candidates/exclusion decisions, shows their original timestamp and previous-batch notice, and permits an explicit new preview. It never restores the old preview automatically. Without valid candidates, required-only generation remains available
- Separate stay drafts retain per-ID user choices while the result is alive, but only current required+first-three optional settings are sent. Excluded, out-of-scope and stale candidate IDs cannot leak extra settings into the request. Edit/regeneration/remount clears this result's candidate pool, exclusions, draft and stay/lunch settings; confirmed form POIs retain 5A-1 edit behavior
- Success/error/finally are guarded by generation plus AbortController. Runtime guards check complete echo, identity/roles, original required prefix, date/time continuity, lunch/return, at most one tail optional per day, unique optional visits, declared outcomes/selected date/earlier slot winner, allowed directed edges and 17/29 edge bounds. They do not invent walking values or perform optimization
- Intentional behavior change: candidate refresh/exclude/restore now invalidates the draft because candidates are inputs. Old map/date/transport-mode/Marker operations remain independent and never request or rewrite the draft. `POST /trips/plan` and its fixed Mock budget/weather/transport stay unchanged

### Automated checks — controlled, not live AMap acceptance

| Command/check | Final result |
| --- | --- |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -q` | **240/240 passed**: 210 prior tests plus 30 optional-scheduling tests |
| `cd frontend && npm run test` | **21 files / 558 tests passed**, including existing search/binding/walking/transit regressions |
| `cd frontend && npm run lint` | Passed |
| `cd frontend && npm run typecheck` | Passed: Next type generation and TypeScript |
| `cd frontend && npm run build -- --webpack` | Passed; verified all modified frontend business sources predate the build used in Chrome |
| `git diff --check` and new-file whitespace checks | Passed |
| Python pure results → actual TypeScript response guard | **120/120 additional controlled cases passed**, including 0–6 required, 0–3 optional, 1–3 dates, lunch, zero movement, time/route failure and deadline records |

- New coverage includes same-batch required visit invariance and exact day fallback, failed-first/later-candidate success, cross-day dedup and one/day, old-return lunch removal vs prefix lunch preservation, empty required/input rejection, shared memo/deadline/pacing/cancellation, 29/58 caps, input snapshot mutation, excluded/reappearing IDs, retained old candidate source, old success/error/finally and synchronous refresh/exclude followed immediately by an old query closure
- Existing Starlette/httpx deprecation warning remains. Backend has no separate configured lint/typecheck script. No test depends on real AMap data; historical Turbopack restrictions were not retried or claimed fixed. Cross-language scripts are evidence-only in the directory below, not another implementation copy

### Actual Chrome acceptance — real candidate and walking responses

- Chrome **154.0.8037.93**, `localhost:3000` and `127.0.0.1:8000`. Restarted only the verified agent-owned project services to load the final implementation; FastAPI and Webpack production startup were clean. Both services remain running for review
- Accommodation **静安寺**, required **武康大楼**, interest **摄影 → 公园**, October 10, 2026, 08:00–20:00. Real candidate response had 18 optional POIs; first three were **延中广场公园**, **辅德里公园**, **静安雕塑公园**, with 15 clearly outside this trial. Then deliberately removed the required POI via the form and regenerated October 10–12 to test optional-only behavior
- Evidence directory: `/private/tmp/trippilot-5b2-evidence.XeuT9L/`. All 11 screenshots were opened and visually checked. `12-safe-request-evidence.json` contains normalized local business requests/responses and safe observations, no credentials/headers/SDK URLs/raw HAR

| Test | Result | Observation / screenshot |
| --- | --- | --- |
| Explicit fetch and full input | Passed | Result entry had no candidate/preview call. One explicit candidate fetch produced the real first-three list, names/addresses and 15-item scope notice. `01-candidate-inputs.png` |
| Real required-first tail addition | Passed | Eight directed edges all valid; required 武康 stayed **08:36–09:36**. Added 延中广场 **10:35–11:35**, returned **13:44**. Adopted **2156s→36min**, **3498s→59min**, **2618s→44min** matched interface and echoed snapshot. `02-real-tail-timeline.png` |
| Lunch, return and daily cap | Passed | Wait 11:35–12:00, lunch 12:00–13:00, real return 13:00–13:44. Other two candidates were `day_slot_used`, not falsely judged infeasible. `03-daily-optional-outcomes.png` |
| Optional stay too long | Passed | Set all three optional stays to 480; draft disappeared and request count remained 1 until explicit generation. Next response retained 武康 **08:36–09:36**, returned **10:12**, and each optional had the dated `time_window` reason. `04-optional-too-long-required-preserved.png` shows required preservation; JSON records the reasons |
| Exclude/restore | Passed | Removed/restored 延中广场; first-three selection changed then returned. Draft stayed cleared; preview requests remained **2→2→2**, no extra route request. `05-exclude-restore-invalidates.png` |
| Edit/regeneration lifecycle | Passed | Accommodation/required confirmation retained on edit. After explicitly removing the required POI and regenerating, old candidates/draft were absent; empty-input generation disabled until a new explicit candidate fetch. Optional stays reset from 480 to default60 |
| No required, three real dates | Passed | Six directed estimates reused across three days; one different optional each on October 10/11/12, returned **10:28 / 09:57 / 09:56**. No required-completion heading and no copied Mock activities. Day-3 click kept preview count **3→3**. `06-optional-only-three-days.png` |
| 400px layout | Passed, simulated | Viewport/document both 400px at 400×820; names, addresses, controls and full day-3 timeline/return wrap without horizontal overflow. `07-narrow-inputs.png`, `08-narrow-timeline.png` |
| Existing binding and real route | Passed | Separately bound Mock 外滩漫步→武康 and 午餐与休息→静安寺; one explicit walking response **2691m / 2153s / 5 geometry segments**. Actual basemap/roads/labels/attribution and blue route visible, both endpoints in full-route view. `09-existing-map-walking.png` |
| Existing activity ↔ Marker, draft isolation | Passed | Activity action centered/red-highlighted Marker1; full-route then Marker2 click focused 午餐与休息, Marker1 normal/Marker2 highlighted. Real line and day-3 draft retained, preview count still 3. `10-activity-marker-regression.png`, `11-marker-activity-regression.png` |

- This browser navigation observed **4 POI search GETs, 2 Mock plan POSTs, 2 candidate POSTs, 3 preview POSTs and 1 legacy walking POST**, all local HTTP 200. Preview edge records were 8 / 8 / 6; those counts are logical normalized results, not individually observed upstream HTTP attempts. No transit or batch stress request was made
- Console: **0 errors, 2 warnings**, not claimed resolved. Initial automation used a screen-reader-only checkbox and two outdated accessible names; corrected against the actual UI and did not count failed operations as acceptance. An attempted cross-call receipt variable was not persistent; final evidence uses explicit response waits and filtered same-navigation resource entries instead

### Limits and handoff

- No focused acceptance blocker remains. No real upstream fault was induced or observed in this 5B-2 session; failure/timeout/partial-source/retry/race cases use controlled tests, not claims of real fault acceptance. Live first-candidate failure followed by later-candidate selection was not separately forced; deterministic tests cover it
- Six required plus three optional / 29-edge / 58-attempt live full-load remains **unverified**; previous six-required-only live acceptance was also not added. Search cutoff is 20 seconds **plus cancellation finalization**, pacing is **request-local**, and browser-local counts do not prove exact upstream attempt counts
- Physical phone/touch, broader browsers/devices, dedicated real subway/transfer samples and historical Turbopack/font/dependency limitations remain separate. This is fixed-order, required-first, one-optional-per-day walking reference planning, not optimal routing, opening/booking verification, budget feasibility or a future-date guarantee
- No new map feature, transit scheduling, LLM, OR-Tools, persistence, dependency, key/proxy/CORS change, staging, commit or push. Stop here; no subsequent milestone

### Actual changed files (19; excludes five preserved pre-existing caches)

- Backend modified (5): `backend/app/schemas/schedule.py`, `backend/app/services/schedule.py`, `backend/app/services/schedule_routes.py`, `backend/app/api/schedule.py`, `backend/tests/test_schedule.py`
- Backend added (1): `backend/tests/test_schedule_optional.py`
- Frontend contract/state/integration modified (8): `frontend/src/types/schedule.ts`, `frontend/src/lib/schedule-api.ts`, `frontend/src/lib/schedule-api.test.ts`, `frontend/src/lib/use-schedule-preview.ts`, `frontend/src/lib/use-schedule-preview.test.tsx`, `frontend/src/lib/use-candidate-pool.ts`, `frontend/src/lib/use-candidate-pool.test.tsx`, `frontend/src/components/trip-plan-result.tsx`
- Frontend UI/tests modified (4): `frontend/src/components/trip/schedule-preview.tsx`, `frontend/src/components/trip/schedule-preview.test.tsx`, `frontend/src/components/trip/candidate-preparation.tsx`, `frontend/src/components/trip/candidate-preparation.test.tsx`
- Documentation modified (1): `docs/PROGRESS.md`

## Milestone 5B-1 Actual Implementation and Acceptance (2026-10-06)

### Baseline, scope and data flow

- Read root/frontend AGENTS, PROJECT_CONTEXT, PROGRESS and local Next.js client-component documentation; implemented directly in `/Users/zijing/projects/trippilot` at `3ed0c44e4a3ec1136161cb275c56947d4f15845b`. No temporary project copy, dependency, environment, key, SDK proxy or CORS changes. All five pre-existing Python cache hashes remain unchanged
- Confirmed requirements → independent stay/lunch settings → explicit “生成步行草案” → `POST /trips/schedule-preview` → direct `AmapWalkingClient` calls → pure `build_schedule(request, edges, generated_at)` → validated response → independent daily preview. No HTTP self-calls, optional candidate selection, route matrix, reorder optimization, real weather, budget recalculation, LLM, OR-Tools or persistence
- The old overview, fixed two-day timeline, original budget/weather and activity map are explicitly grouped as “旧 Mock 行程示例”. They are not sources for the new preview. Candidate preparation remains separate and does not automatically bind or schedule places

### Contract and deterministic rules

- Independent strict request: `start_date`, `end_date`, `daily_start_time`, `daily_end_time`, `accommodation_place`, `must_visit_places`, `duration_settings: [{ place_id, minutes, source }]`, `lunch: { enabled, start_time, end_time }`. Reuses complete ConfirmedPlace snapshots and existing walking endpoint validation; rejects extra fields, duplicate IDs, invalid coordinates, missing/extra stay IDs and arbitrary client route minutes/keys/URLs. Safe 422 never reflects the raw input
- Supports 1–3 inclusive calendar dates and 1–6 required places; no truncation. Settings are separate from Place: integer 15–480 minutes, default 60/source `default`; any user edit becomes `user` even when changed back to 60. Default provenance cannot claim a non-60 value. An absent accommodation, empty list or over-limit list prevents UI generation with a scope prompt, not an impossibility claim
- Local clocks use Shanghai minute precision. Enabled lunch defaults to 12:00–13:00 and must lie wholly inside the daily window with increasing bounds; no silent clipping. Disabled lunch does not constrain the schedule. The UI retains its disabled draft but sends valid inactive clocks, so closing an invalid lunch draft unblocks generation
- Try must-visits in input order on the current date. Each travel estimate uses `ceil(duration_seconds / 60)` for both scheduling and display. Walking and visits are indivisible blocks, do not overlap lunch, and may require an explicit wait followed by the full lunch reservation. No restaurant, detour, reservation, price or inferred visit length is invented
- Before committing a visit, also simulate its real return to accommodation, including lunch avoidance. If it cannot fit, finish the verified prior prefix and try the same place from accommodation on the next date. A known-too-long stay can roll over before an unused adjacent edge failure is considered. Otherwise a required missing/failed edge stops the current attempt, not an automatic reroute onto another day
- When dates run out or a required route is unavailable, keep the verified prefix and return. The current place receives `time_window`, `route_timeout`, `no_route`, `route_data_error` or `route_failed`; every later place is `current_order_not_continued`, not independently declared infeasible. Unused edge failures do not invalidate a completed preview
- Empty requested dates remain present with `items: []` and no return time or fabricated lunch. An accommodation POI that is also required still gets its visit/stay; same ID or equal normalized coordinates creates explicit zero movement without an upstream query
- Response has `complete | partial | unscheduled`, complete request echo, generation time, dated walk/visit/wait/lunch items, return times, unscheduled reasons, edge results and rules/unknowns. Edge records retain endpoints, original seconds, ceiling minutes, distance, query time and adoption flag; `source="same_place"` is a local identity decision, not a claim of an AMap request. No costs/weather or `is_mock=true` TripPlan wrapper is used

### Route budget and the live QPS correction

- Query only accommodation→each required POI, each required POI→accommodation and originally adjacent required pairs. Deduplicate directed endpoint identity plus six-decimal coordinates; never substitute the reverse direction. At most 17 logical nontrivial edges for six distinct places, at most three concurrent. No cross-generation cache
- Search deadline is 20 seconds for the batch, including queue and pacing waits. On deadline or parent cancellation, cancel/drain outstanding tasks; finalization may add a little response time, so 20 seconds is not an exact response-time promise. Frontend timeout is 25 seconds. Existing connection-only one retry remains unchanged: at most 34 HTTP attempts, no new retry layer or automatic frontend retry
- The first actual browser batch returned three valid estimates and two business failures; the UI correctly preserved a one-visit roundtrip prefix. A separate five-request bounded diagnostic exposed only HTTP status, validated numeric business code and a rate-limit boolean: 3×10000, 2×10021, all HTTP 200. [AMap's official error reference](https://lbs.amap.com/api/webservice/guide/tools/info) identifies 10021 as account/service QPS rejection. No raw payload, key or credential-bearing URL was printed or retained
- Concurrency alone allowed five quick requests within a second. The minimal fix adds a batch-local monotonic-clock lock and at least **0.4 seconds between nontrivial logical starts**. Same-place edges bypass it. No existing AMap client, proxy or credential changes. Four controlled tests cover the fast-response rate-limit reproduction, deadline cleanup while paced, parent cancellation and same-place bypass
- Pacing reduces this batch's launch bursts; it is not an account-wide rate limiter. Other users/processes and the original connection retry may still consume quota or create additional instantaneous HTTP attempts. External availability and future quota are not guaranteed

### Frontend lifecycle and validation

- `useSchedulePreview` owns result-local settings and state, not the candidate pool or daily map. Only explicit generation calls fetch. A synchronous pending guard blocks duplicate clicks. Any stay/lunch setter immediately aborts, increments the request version and clears the old preview before updating the settings ref/state
- Success, error and finally paths all check their generation. Changing A→B→A settings cannot revive an earlier response or release a newer request's pending guard. Failure never restores the previous preview as current. Editing synchronously invalidates, unmount aborts, and a new result/refresh resets all preview settings; confirmed form requirements still follow 5A-1 edit retention
- Request validation and response guards check the exact request echo, all requested dates, input-order visit prefix plus unscheduled remainder, continuous time bounds, lunch exclusion, accommodation returns, referenced edges, `ceil` minutes, provenance and normalized coordinates. Guards do not invent or optimize routes. The legacy search-failure regression assertion was scoped to the search region so the new preview validation hint cannot accidentally satisfy it
- Preview date tabs only change the display. Candidate fetch/exclude/restore, old date/mode selection, activity bindings and Marker operations cannot change or regenerate the preview. No global store, storage or new map functionality

### Automated checks — controlled HTTP and SDK, not live acceptance

| Command/check | Final result |
| --- | --- |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -q` | **210/210 passed**, after the QPS fix: 165 original + 44 schedule tests + one 150-seed invariant test |
| `cd frontend && npm run test` | **21 files / 498 tests passed**: 381 original + 66 API/contract + 31 hook + 20 UI/lifecycle |
| `cd frontend && npm run lint` | Passed |
| `cd frontend && npm run typecheck` | Passed: Next type generation + TypeScript |
| `cd frontend && npm run build -- --webpack` | Passed; this production build was used in Chrome. Only backend pacing changed afterward |
| `git diff --check` plus new-file whitespace checks | Passed |
| Backend output → actual TypeScript guard | 36 additional controlled cross-language cases passed, including 1/2/3 days, rounded coordinates, same-place, lunch, partial and failed edges |

- Controlled tests cover date counts independent of Mock days, original-order prefixes, forward/return availability, exact lunch/end boundaries, next-day retry, wait/lunch non-overlap, absent/duplicate/over-limit inputs, explicit stay provenance, invalid response data, same-point visits, unused vs required failures, 17-edge/34-attempt bounds, three-way concurrency, deadlines/draining and parent cancellation
- The 150-seed pure-function check also verifies immutable input snapshots/edge data, deterministic repeat output, continuous day clocks, accommodation return, lunch exclusion, prefix tracking and exact used-edge flags. Frontend race tests cover old success/error/finally, edit/unmount, invalid lunch closure, duplicate submit and A→B→A inputs. Existing candidate/binding/route/Mock regressions remain passing
- No live requests are made by these automated tests. Existing Starlette/httpx deprecation warning remains; backend has no separate lint/typecheck script. The historical Turbopack restriction was not retried or claimed fixed; established Webpack was used

### Actual Chrome acceptance — live POIs and walking estimates

- Used the existing **Chrome 154.0.8037.93** connection, `localhost:3000` and `127.0.0.1:8000`. Restarted only verified agent-owned project services to load this implementation; final FastAPI startup was clean and the final frontend build was served. Services remain running for review
- Explicitly searched and confirmed **静安寺** as accommodation, **武康大楼** and **上海图书馆(淮海路馆)** as required places. This is a reference point, not a hotel reservation. One-day request October 10, 2026, daily window 10:30–18:00; afterward edited the same confirmed request to October 10–12
- Evidence directory: `/private/tmp/trippilot-5b1-evidence.4DdnP0/`. All 11 PNGs were opened and visually checked. `13-safe-request-evidence.json` contains public normalized POI/preview data, safe paths/statuses and observations only, not headers, credentials, SDK URLs or raw HAR

| Test | Result | Actual observation / evidence file |
| --- | --- | --- |
| Explicit generation only | Passed | Entering the first result produced zero preview POSTs. After each edit, preview was absent until clicked. Safe request evidence |
| Initial real partial failure | Observed, diagnosed and fixed | First batch used the valid 武康大楼 prefix and returned at 14:36; library marked route unavailable, not fabricated. `02-live-partial-before-pacing.png`; safe diagnostic codes |
| Complete one-day real preview after pacing | Passed | All five edges `ok`; adopted 静安寺→武康 **2156s→36min**, 武康→图书馆 **518s→9min**, 图书馆→静安寺 **1699s→29min**. Two 60-minute visits; return **15:38**. Every displayed duration matched the normalized response. `05-real-one-day.png` |
| Lunch and waiting | Passed | Walk 10:30–11:06, wait 11:06–12:00, lunch 12:00–13:00, visit 13:00–14:00, walk 14:00–14:09, visit 14:09–15:09, return 15:09–15:38; no overlap. `05-real-one-day.png` |
| Settings invalidate without request | Passed | Changing first stay to 480 immediately removed timeline; request count stayed 2 until explicit click. `03-rule-change-clears.png` |
| Unscheduled reasons | Passed | Explicit 480-minute generation returned `unscheduled`; 武康 reason `time_window`, library `current_order_not_continued`, empty day/no fake lunch. All five routes were valid, so failure was not mislabelled as network failure. `06-unarranged.png` |
| Edit retention / regeneration reset | Passed | Both confirmed places and accommodation retained on edit. New three-day result had no preview and default60/default provenance, not prior 480-minute setting; no automatic query. Safe request evidence |
| Three actual dates / date-only switch | Passed | October 10/11/12 present; days 2/3 empty with null return, no copied Mock activity. Clicking day 3 added no request. Legacy Mock still had only its independent two dates. `07-three-days-empty-day.png` |
| 400px layout | Passed, simulated | 400×820 viewport/document width400; settings, date controls, times/long names and warnings wrap. `04-narrow-settings.png`, `10-narrow-timeline.png`. `11-narrow-map.png` shows usable map/route controls, but is not evidence that both endpoints fit that narrow screenshot |
| Existing activity binding / real map route | Passed | Bound 外滩漫步→武康、午餐与休息→静安寺 separately; one real walking request **2691m/2153s/5 geometry segments**. Basemap, roads, labels, attribution and real blue route visible. Both endpoints fit desktop view. `08-existing-map-route.png` |
| Existing activity ↔ Marker | Passed | Activity action centered/highlighted Marker1 with the route retained; after fit-all, clicking Marker2 focused 午餐与休息 and selected it. No extra route or preview requests. `09-activity-marker.png` records the activity→Marker leg; reverse action verified live |
| Refresh cleanup | Passed | Reload returned to blank form: no preview, no result-local bindings/route or confirmed POIs; no new business request. `12-refresh-clears.png` and safe observations |

- This session observed **5 POI searches, 2 Mock plan POSTs, 4 preview POSTs and 1 legacy walking POST**, all local business HTTP 200. Preview statuses: initial partial, then complete/unscheduled/complete after pacing. Each preview reported five directed edges. No candidate or transit request was made in this round
- Browser batches did **not** individually trace server→AMap HTTP attempts. The separate diagnostic observed exactly five upstream attempts; the general 17/34 maxima and retry behavior are code/controlled-test evidence, not an inferred live attempt count
- Console before final reload: **0 errors, 2 warnings**, not claimed resolved. An initial automation wait used the wrong candidate-list name and another waited for complete when the real response was partial; neither was counted as a passed operation. A screenshot path restriction was handled by moving generated screenshots out of the repo

### Limits and handoff

- No known blocking issue remains in the focused 5B-1 acceptance. Real 10021 rejection was observed and the paced version rechecked successfully; this is not a comprehensive real-fault campaign. Timeouts, no-route, malformed upstream payloads, six-place limits, multi-day spillover and races rely on controlled tests, not forced live faults or a six-place live run
- Batch pacing is local, not a global quota guarantee. Twenty seconds is a search deadline plus cancellation finalization, not a guaranteed exact response time. Current real queries are reference estimates, not verified future-date traffic
- Physical phone/touch, broader devices, dedicated real subway/transfer samples and historical Turbopack/font/dependency limitations remain pending/separate. No booking, opening/appointment feasibility, meal detour/price, budget sufficiency or optimal route is claimed
- Optional candidates remain outside scheduling. The preview does not rewrite confirmed inputs, old Mock times/transport/budget/weather, activity bindings or map routes. No subsequent milestone, staging, commit or push

### Actual changed files (17; excludes five preserved pre-existing caches)

- Backend added: `backend/app/schemas/schedule.py`, `backend/app/services/schedule.py`, `backend/app/services/schedule_routes.py`, `backend/app/api/schedule.py`, `backend/tests/test_schedule.py`, `backend/tests/test_schedule_invariants.py`
- Backend modified: `backend/main.py` (router registration only)
- Frontend added: `frontend/src/types/schedule.ts`, `frontend/src/lib/schedule-api.ts`, `frontend/src/lib/use-schedule-preview.ts`, `frontend/src/components/trip/schedule-preview.tsx`
- Frontend tests added: `frontend/src/lib/schedule-api.test.ts`, `frontend/src/lib/use-schedule-preview.test.tsx`, `frontend/src/components/trip/schedule-preview.test.tsx`
- Frontend modified: `frontend/src/components/trip-plan-result.tsx` (independent hook/panel, edit invalidation and explicit Mock grouping), `frontend/src/components/trip-plan-result.test.tsx` (scope existing search-error assertion)
- Documentation updated: `docs/PROGRESS.md`

## Milestone 5A-2 Actual Implementation and Acceptance (2026-10-06)

- Subsequent closeout: committed/pushed as `3ed0c44e4a3ec1136161cb275c56947d4f15845b` before the authorized 5B-1 task. The no-commit/push statements below describe the historical implementation handoff; evidence and limitations remain intact

### Baseline, scope and data flow

- Read applicable root/frontend AGENTS, PROJECT_CONTEXT, PROGRESS and local Next.js client-component guidance. Implemented directly in `/Users/zijing/projects/trippilot` at `58cecc300201bf83785a69f520a9b525a1e19eb4`. Preserved the five pre-existing Python caches; no temporary implementation copy, staging, commit or push
- Flow: submitted confirmed requirements → whole-trip candidate panel after ConfirmedPlaces and before day selection → explicit “获取候选地点” → `POST /places/candidates` → existing `AmapClient.search` calls → bounded aggregation/deduplication → frontend runtime validation → independent candidate/exclusion state. No automatic initial request or request on day/mode/Marker changes
- Reused `ConfirmedPlace`, the existing AMap client/dependencies and connection-only retry, `PlaceDetails`, React/fetch and current test frameworks. `backend/main.py` only registers the independent candidates router; no dependency, key, environment, proxy or CORS changes. Original search/trip/walking/transit APIs, activity binding and map logic remain intact
- Accommodation is required by this new endpoint; legacy text-only results prompt returning to confirm a reference point and do not guess coordinates. Accommodation identity is used to exclude it from optional visits, not to imply proximity ranking. Must-visits always derive from submitted confirmed snapshots, including before a search or after failure

### Contract, retrieval budget and deterministic selection

- Request contains only `{ accommodation_place, must_visit_places, interests }`. Accommodation is required; missing must-visits/interests default to empty lists. Strict complete ConfirmedPlace validation and must-visit ID uniqueness are reused. Unknown interest, arbitrary city/keyword/upstream/Key or extra fields are rejected with safe 422; malformed coordinates do not leak raw input. Missing server configuration retains the existing safe 503
- Fixed canonical order and mapping: **摄影 → 公园; Citywalk → 步行街; 美食 → 餐厅; 建筑 → 历史建筑; 博物馆 → 博物馆; 购物 → 商场**. Repeated interests/keywords are merged. No interests produces one **旅游景点** query with `interest: null`, displayed as **通用候选**
- At most **6 logical searches**, **3 concurrent**, each using existing fixed AMap v5 keyword search for **上海**, **page 1 / page size 20**, without pagination. The existing client alone may retry a connection failure once, so at most **12 upstream HTTP attempts per six-query batch**. There is no aggregation-layer retry or frontend auto-retry
- `asyncio.wait` imposes a **12-second whole-batch search deadline**, including semaphore wait time. On expiry, cancel and drain running/queued tasks; parent cancellation also cancels/drains children. Already completed results remain available. Cancellation cleanup and serialization may add brief finalization time beyond the search deadline. Frontend timeout is **15 seconds**
- Candidate metadata is separate from Place: `{ place, role: "must_visit" | "optional", retrieval_sources: [{ interest, keyword }] }`. No metadata is inserted into the strict 5A-1 ConfirmedPlace shape. Only original upstream category/address/coordinates are displayed; no invented ticket, hours, duration, ratings or prices
- Merge by **POI ID**, not name. Must-visits retain all original snapshot fields and input order, with any search sources added only to metadata. Accommodation is omitted unless also a must-visit. Cross-query optional matches retain the first snapshot in canonical query order and merge every matching retrieval source before limiting the output
- Select optional POIs round-robin in canonical interest order: each query contributes its next eligible, unselected POI in upstream order. Skip already-selected IDs, accommodation and required IDs; stop at **18 optional**. Must-visits are not capped. Neither asynchronous completion order nor the first interest can decide the entire pool
- Response: `{ status: "success" | "partial" | "failed", queried_at, keywords, queries, candidates }`. Each query includes interest/keyword, success/failed/timeout, valid result count and nullable safe message. Successful empty searches count as success, not failure; all failed searches remain failed even when required POIs are present. Upstream raw errors/URLs/credentials are never reflected

### Frontend state and tradeoffs

- `use-candidate-pool.ts` lives at the result level, outside the date-keyed PlaceExplorer. Confirmed must-visits are immediately visible and cannot be excluded here. Optional exclusion/restoration changes only a local ID Set, not requests, submitted requirements, bindings, routes, times or budget
- During refresh, keep the last valid pool and exclusions with a clear updating message and a synchronous duplicate-submit guard. A failed response or transport error preserves that pool; the UI labels it as the previous result. `response` holds the displayed valid batch, while `lastAttempt` holds the latest returned per-query status, so old candidates and new errors are not misrepresented as one successful batch
- Success or partial success replaces optional entries with this batch only; partial failures are explicitly shown. Successful zero-option batches clear old optional entries and say there are no new optional places. Excluded IDs remain remembered within the same result even if temporarily absent, so later retrieval cannot silently restore them
- AbortController plus monotonically increasing request version guard both old successes and old errors. The result's edit handler synchronously calls `candidates.invalidate()` before unmount; cleanup aborts again safely. Regeneration/remount starts a new empty optional pool/Set; 5A-1 form confirmations remain on edit and clear on refresh. No global state or persistence
- `getActivePlaces()` returns required plus non-excluded optional Places for a future consumer; it is not wired into a planner in this milestone. No candidate automatically binds a Mock activity
- Avoid-place text is echoed with an explicit “not automatically filtered yet” notice and manual exclusion guidance. Interest keywords indicate retrieval provenance only: not optimal recommendations, precise preference satisfaction, budget compliance or nearest-to-accommodation ranking. Ordinary user-facing copy avoids task/thread/version terminology

### Automated checks — mocked AMap HTTP/SDK

| Command | Result |
| --- | --- |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -v` | Passed: **165/165** (140 existing + 25 new candidate tests) |
| `cd frontend && npm run test` | Passed: **18 files / 381 tests** (300 existing + 47 API/contract + 15 hook + 19 UI/lifecycle tests) |
| `cd frontend && npm run lint` | Passed |
| `cd frontend && npm run typecheck` | Passed: Next type generation + `tsc --noEmit` |
| `cd frontend && npm run build -- --webpack` | Passed: compilation, TypeScript, static generation and traces; the final build after all source changes was used in Chrome |
| `git diff --check` and new-file whitespace checks | Passed |

- Backend controlled tests cover mapping/default/deduped interests, fixed upstream/city/page, six-query/twelve-attempt budgets, max-three concurrency, whole-batch timeout including queued cancellation/drain, parent cancellation, deterministic completion-independent round-robin, all must-visits beyond 18, original snapshots, shared accommodation/must identity, same-name distinct IDs, merged sources, successful empty/partial/all-failed and safe input/errors/logging
- Frontend controlled tests cover strict request/response snapshot and metadata validation, real numeric coordinates, query/source/count/status consistency, invalid timestamp/status/role, 18-option cap, 15-second timeout and safe errors; idle/no-auto/duplicate guards; old pool preservation and replacement; exclusions across disappearing/reappearing IDs; both late success/error paths on invalidate/unmount
- Real component tests cover original Mock immutability, required roles, empty/default/legacy states, exclude/restore without network or route/marker changes, day/mode/Marker retention, actual Form→Planner edit/regeneration with retained confirmations, cleared candidate/binding/route state and stale responses. All prior 4A/4B/4C/5A-1 coverage remains passing
- Tests use mocked HTTP/SDK and isolated dotenv; they consume no live quota. Backend retains the existing Starlette/httpx deprecation warning and has no configured standalone lint/typecheck script. The historical Turbopack limitation was not retried or claimed fixed; the established Webpack path was used

### Actual Chrome acceptance — live, separately evidenced

- Used the existing connection to **real Chrome 154 on macOS**, with no new browser tooling/dependencies. Reused `localhost:3000` and `127.0.0.1:8000`; restarted only the verified prior agent-owned processes for the current FastAPI and final Webpack build. FastAPI startup was clean. These services remain running for review
- Entered October 10–11 2026, budget 3000, two travelers, **摄影 + 建筑**. Explicitly searched and confirmed **静安寺** as accommodation and **武康大楼** as required. Avoid-place text was “大型商场、排队过久的地点” and visibly remained unfiltered text guidance
- First explicit candidate POST returned **200 / success**, actual keywords **公园、历史建筑**, both successful with **20** valid POIs. Returned **1 must-visit + 18 optional**, all unique IDs. 武康大楼 also appeared in the live historical-building search: its original confirmed snapshot remained one required item with added retrieval source, not an extra optional item. Snapshot equality was checked across all Place fields
- Two candidate batches were deliberately requested: the first acceptance query and a fresh query after regeneration to prove old exclusions were cleared. Each had two logical searches; both returned 200/success and 1+18 candidates. Other live business traffic was **4 explicit POI searches, 2 plan POSTs and 1 walking POST**, all 200; **zero transit queries**. Exclude/restore/day/mode/Marker actions added no candidate or route request
- Budget evidence distinguishes observation from bounds: Chrome directly observed **2 candidate POSTs × 2 reported logical searches**, no automatic requests and no pagination controls. It did not trace individual server→AMap attempts. Existing retry code and controlled tests establish at most 4 attempts for each of these two-interest batches, and at most 12 for six interests; no unobserved exact upstream attempt count is claimed
- Evidence directory: `/private/tmp/trippilot-5a2-evidence.AShgqn/`. All 11 screenshots were opened and visually checked. `12-safe-request-evidence.json` contains only local business paths/statuses, normalized public POI/request/response data, counts and observations; no keys, headers, environment content, SDK URLs or raw HAR. Browser-tool diagnostic artifacts were moved outside the repo and are not shared evidence

| Test | Result | Observation / screenshot |
| --- | --- | --- |
| Enter result without automatic retrieval | Passed | Required 武康大楼 visible, optional count 0 and explicit button; Network had no candidate POST. `5a2-01-before-explicit-query.png` |
| Explicit real retrieval, roles and provenance | Passed | 200 success; names, addresses, original categories, sources and two-query detail match the response; required item has no exclude control. `5a2-02-real-candidates.png` |
| Exclude / restore without requests | Passed | 延中广场公园 changed to excluded and back to retained; repeated local exclusion left 18 optional cards, one excluded. Candidate POST count remained 1 and route count 0 at this point. `5a2-03-excluded.png`; safe request evidence |
| Day and mode retention | Passed | Day 2 retained all 18 optional items and the exclusion; return to Day 1 and walking→transit→walking did not fetch candidates or routes. `5a2-04-day2-keeps-candidates.png` |
| 400px responsive controls | Passed, simulated | At **400×820**, document width was 400, long names/addresses and controls wrapped. Restore/exclude remained usable. `5a2-05-narrow-candidates.png` |
| Existing binding / real walking regression | Passed | Independently bound adjacent 外滩漫步 → 武康大楼 and 午餐与休息 → 静安寺, then explicitly queried once: **2691m / 2153s / 5 segments**, displayed **2.69公里 / 预计36分钟**. Excluding/restoring a candidate afterward preserved both bindings and full real blue geometry, roads/labels/attribution. `5a2-06-real-route-after-exclusion.png` |
| Existing activity ↔ Marker regression | Passed | Clicking 外滩漫步's map action centered/highlighted Marker 1 (0px/0px center offset); after full-route fit, clicking 静安寺 Marker focused 午餐与休息. Pool stayed 18 with one excluded and no extra request. `5a2-07-activity-marker-regression.png` records the centered Marker 1 and retained route; reverse direction also checked live |
| Edit retains confirmed form requirements | Passed | Candidate module disappeared; 静安寺、武康大楼 and both interests remained in the form. `5a2-08-edit-keeps-confirmations.png` |
| Regeneration clears pool/bindings/routes | Passed | Fresh result initially had zero optional candidates, no old binding actions or route control, and no automatic candidate query. Required 武康大楼 remained. `5a2-09-regenerated-empty-pool.png` |
| New result does not inherit exclusion decisions | Passed | Explicit second retrieval returned previously excluded 延中广场公园 as retained; zero restore buttons and all 19 places retained. `5a2-10-new-result-clears-exclusions.png` |
| Actual browser refresh clears result state | Passed | Reload from a populated pool returned to the blank form, no result/candidate panel and no confirmed accommodation/must-visits. `5a2-11-refresh-clears.png` |

- Console before refresh: **0 errors, 4 warnings**; warnings were not claimed resolved. No feature defect was observed in the live acceptance. Tool-level selector mismatches were corrected against actual DOM roles/text before retrying; failed automation actions were not counted as acceptance. Screenshot tool paths were restricted to the repo, so generated PNGs were subsequently moved to the evidence directory

### Limits and handoff

- Partial/all-failed batches, timeouts, connection retries, disappearing/reappearing excluded IDs and adversarial races were verified with controlled tests, not manufactured against live AMap. No claim of real upstream fault acceptance or uninterrupted external connectivity
- Physical phone/touch and broader devices remain pending. Dedicated live subway/transfer samples, historical Turbopack/font/dependency/environment limitations remain below; this round did not repeat transit live acceptance or fix those separate concerns
- This is a city-wide keyword candidate pool, not semantic recommendations or scheduling. Required snapshot/source claims are not independently reverified. No route matrix, automatic transport, budget rewrite, weather, LLM, database, persistence or 5B work
- Final scope/security checks: all 15 deliverables exclude actual configured credentials; built frontend assets contain no backend Web Service key or JS security code. Five original cache hashes remain unchanged. No dependency/environment/configuration/proxy/CORS changes; nothing staged, committed or pushed

### Actual changed files (15; excludes five preserved pre-existing caches)

- Backend added: `backend/app/schemas/candidates.py`, `backend/app/services/__init__.py`, `backend/app/services/candidates.py`, `backend/app/api/candidates.py`, `backend/tests/test_candidates.py`
- Backend modified: `backend/main.py` (router registration only)
- Frontend added: `frontend/src/types/candidates.ts`, `frontend/src/lib/candidates-api.ts`, `frontend/src/lib/use-candidate-pool.ts`, `frontend/src/components/trip/candidate-preparation.tsx`
- Frontend tests added: `frontend/src/lib/candidates-api.test.ts`, `frontend/src/lib/use-candidate-pool.test.tsx`, `frontend/src/components/trip/candidate-preparation.test.tsx`
- Frontend modified: `frontend/src/components/trip-plan-result.tsx` (result-level hook, synchronous edit invalidation and panel placement only)
- Documentation updated: `docs/PROGRESS.md`

## Milestone 5A-1 Actual Implementation and Acceptance (2026-10-06)

- Subsequent closeout: committed/pushed as `58cecc300201bf83785a69f520a9b525a1e19eb4` before the explicitly authorized 5A-2 task. The no-commit/push statements below describe historical implementation/acceptance; evidence and limitations are preserved

### Baseline, scope and data flow

- Read the applicable root/frontend AGENTS, PROJECT_CONTEXT, this progress file and local Next.js client/form guidance. Worked directly in `/Users/zijing/projects/trippilot` at `cb1a2605af312b9c8dcb319358615691ca56b0be`, not a temporary copy. Preserved all five pre-existing Python caches, including the three already-untracked files; no staging, commit or push
- Flow: user opens one requirement picker → explicitly submits a Shanghai search through existing `GET /places/search` → previews a candidate → explicitly confirms it into form state → form derives legacy text from confirmed names → `POST /trips/plan` → strict Pydantic request/echo → frontend runtime validation → confirmed requirements shown alongside, not applied to, the existing Mock result
- Reused the existing `Place` field semantics and search request module, `PlaceSearch`, React/fetch and test frameworks. Added a shared text-only `PlaceDetails` display and one on-demand requirement picker; no requirement map was needed. No dependency, lockfile, environment, key, proxy allowlist, CORS or persistence changes
- Accommodation is a required **住宿参考点** in the new UI, not a hotel booking. Hotels or real landmarks may represent the general area. Must-visit POIs are optional and support explicit add/replace/remove; duplicate IDs are blocked while same-name/different-ID places remain distinct with their addresses. The same POI can serve both roles
- The result explicitly says these are confirmed submitted requirements, not scheduled stops or validated Mock time/traffic/cost/weather. The client-provided `source="amap"` is a snapshot label, not proof of independent backend verification; no POI-details lookup was added. Avoid-place and other existing inputs remain recorded requirements, not planning claims

### Contract and state decisions

- `TripRequest.accommodation_place` defaults to `null`; `must_visit_places` defaults to `[]`. Legacy text-only requests still return the original Mock example. The new form derives `accommodation_location` from the confirmed accommodation name and ordered `must_visit` names from the confirmed list; there is no second independently editable text representation
- Supplied structured places require all existing fields: `id`, `name`, nullable `address`, numeric `latitude`/`longitude`, nullable `category`, and explicit `source="amap"`. `ConfirmedPlace` rejects extra fields, blank IDs/names, string/boolean/non-finite/out-of-range coordinates and invalid sources. It strips outer ID/name whitespace and validates must-visit ID uniqueness and ordered text/name consistency. No price, opening hours or visit duration was invented
- An explicitly submitted empty must-visit list cannot contradict nonempty legacy text. The response-only `TripRequestEcho` permits the default empty structured list alongside an old text-only request's must-visit text, so legacy echoes remain valid. Nonempty structured places still use the same strict validation. The frontend separately compares all confirmed snapshot fields to the submitted request and enforces text consistency for every new explicit list, including `[]`; malformed, missing or changed echoes cannot be treated as valid confirmed requirements
- The trips route returns a fixed safe 422 for request validation errors, including non-finite input that must not cause JSON error serialization to crash or reflect raw input. Existing Mock itinerary and budget construction were not changed
- Confirmed form values are separate from search drafts. Opening a different target or reopening a cancelled picker creates a new session and clears candidates/selection. Parent session identity plus AbortController and child monotonically increasing request version protect both late successes and errors, including accommodation→must-visit→accommodation and overlapping keywords. Confirmation rechecks the active session and duplicate IDs
- The picker is a sibling of the travel form, not a nested form. Search Enter only searches; it does not generate a trip. An unfinished picker blocks generation with an explicit prompt to confirm or cancel. A valid submission closes/invalidates the picker before hiding the form. Submission failure and returning to edit preserve confirmed values because the existing planner keeps the form mounted; refresh/remount clears them. The result component still unmounts on editing, so 4B bindings and 4C routes cannot carry into a new result

### Automated checks — mocked HTTP/SDK, not live AMap acceptance

| Command | Result |
| --- | --- |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -v` | Passed: **140/140** (119 existing + 21 new tests, including table-driven validation cases) |
| `cd frontend && npm run test` | Passed: **15 files / 300 tests** (244 existing + 33 request/echo tests + 23 form/picker/lifecycle tests) |
| `cd frontend && npm run lint` | Passed |
| `cd frontend && npm run typecheck` | Passed: Next type generation + `tsc --noEmit` |
| `cd frontend && npm run build -- --webpack` | Passed: optimized production build, TypeScript, static pages and traces; this build was used for the Chrome acceptance below |
| `git diff --check` | Passed |

- Backend coverage includes old requests/response round-trip, default null/empty fields, strict complete snapshots, missing/invalid source, illegal coordinates and extra fields, duplicate IDs, same-name distinct IDs, shared roles, text consistency, safe validation errors and unchanged Mock activities/costs
- Frontend coverage includes preview versus confirm, required accommodation, optional/multiple must-visits, ID deduplication, same-name distinct IDs/shared roles, replacement/removal/cancel/reentry, failure/empty-result preservation, stale success and error isolation across sessions/keywords, search Enter, unfinished-session submit prevention, failed submission, echo/runtime validation, edit preservation and remount clearing
- A full component test also confirms requirements → result → explicit activity bindings → walking query → edit/regenerate, asserting old activity bindings/map lines are cleared and the original Mock plan remains intact. Existing 4A/4B/4C walking/transit/map tests remain passing
- These tests make no real AMap requests. Backend still emits the existing Starlette/httpx deprecation warning; no backend lint/typecheck script is configured. Used the established Webpack build rather than retrying or claiming to resolve the historical Turbopack restriction. Final documentation-only work does not represent another run of the whole suite

### Actual Chrome acceptance — live searches and route, not mocks

- Used the existing browser-control connection to real Chrome 154 on macOS, without installing tools or project dependencies. Loaded the newly built production frontend at `http://localhost:3000` and current FastAPI at `http://127.0.0.1:8000`. Restarted only the prior agent-owned service processes after verifying ownership; FastAPI startup was clean. Services remain available for review
- Submitted October 10–11 2026, budget 3000, two travelers. Confirmed accommodation **静安寺** and two must-visits **武康大楼**, **静安寺**. IDs, names, addresses, coordinates, categories and sources were checked from actual search responses through the browser POST body and returned `TripPlan.request`; all fields matched. The accommodation/must-visit shared-POI case was deliberately exercised
- This acceptance made **7 explicitly triggered real POI searches, 2 plan submissions and 1 walking query**, all successful 200 responses. No transit query or synthetic upstream fault was triggered this round. After the first result, requirements had not automatically bound any Mock activity
- Screenshot directory: `/private/tmp/trippilot-5a1-evidence.Apu8wI/`. Each of the 11 screenshots below was opened and visually checked. Shared evidence contains page content, not credentials, environment files, HAR or raw SDK network logs. Browser-tool diagnostic artifacts are kept outside the repository and are not shared as acceptance evidence

| Action / check | Result | Actual observation / screenshot |
| --- | --- | --- |
| Missing accommodation and search Enter | Passed | Generation without confirmation showed the required-reference prompt and no plan request. Enter in the Shanghai search field made only the real POI search, not a plan submission |
| Candidate preview is not saved | Passed | Selected 静安寺 showed “尚未保存”; confirmed accommodation remained empty until the explicit confirm click. `01-preview-not-saved.png` |
| Cancel replacement preserves confirmed value | Passed | Searched/previewed 武康大楼 as replacement, then cancelled; confirmed accommodation remained 静安寺. Reopening a picker had no old candidates. `02-cancel-keeps-lodging.png` |
| Two must-visits and duplicate prevention | Passed | Confirmed 武康大楼 and 静安寺 separately. Searching/selecting 武康大楼 again displayed the duplicate warning and disabled confirmation; cancellation retained both originals. `03-duplicate-blocked.png` |
| 400px form and result | Passed, simulated | At **400×820**, document width remained 400px and inspected controls/cards wrapped without horizontal overflow. `04-narrow-confirmed-form.png`, `05-narrow-result-echo.png` |
| Structured submission and result echo | Passed | Real `POST /trips/plan` 200; search snapshots, submitted IDs/coordinates/names and response matched. Confirmed name/address/source displayed with “not yet used for Mock scheduling” notice. `06-result-echo.png` |
| Existing activity binding and real walking regression | Passed | Independently confirmed Day 1 外滩漫步 → 武康大楼 and adjacent 午餐与休息 → 静安寺, then explicitly queried. `POST /routes/walking` 200 returned **2691m / 2153s / 5 segments**; UI showed **2.69公里 / 预计36分钟**. Full real AMap roads/labels/attribution, bent blue route and both markers were visible. `07-real-walking-regression.png` |
| Existing activity ↔ Marker regression | Passed by live observation | Activity 1 “在地图查看” centered its highlighted Marker (rendered center offset 0px/0px); after fit-all, clicking the 静安寺 Marker focused 午餐与休息. Route control remained and no extra route query occurred. Screenshot 07 records the full route context, not every intermediate selection state |
| Return to edit retains confirmations | Passed | Accommodation 静安寺 and both must-visits remained. `08-edit-keeps-lodging.png`, `09-edit-keeps-must-visit.png` |
| Regenerate clears old bindings/routes | Passed | Second plan request matched the first confirmed requirements. New result had zero bound markers, no retained route/full-route control and disabled missing-endpoint query buttons; requirements did not auto-bind. `10-regenerated-no-bindings-route.png` |
| Actual browser refresh clears form confirmations | Passed | Reload returned to an empty form with no confirmed accommodation/must-visits and no result. `11-refresh-clears-confirmations.png` |

- Console inspection before refresh: **0 errors, 2 warnings**; warnings were not claimed resolved. Only local business request paths/statuses and normalized data were inspected for reporting; no sensitive SDK URLs or key values are included

### Limits and handoff

- No observed blocking 5A-1 defect remains in this focused acceptance. Same-name/different-ID variants, search failures/empty responses and adversarial races were verified with controlled tests, not artificially produced against real AMap. They are not live fault acceptance
- 400px Chrome emulation is not a physical-phone/touch test. Broader devices, dedicated live subway/transfer samples, real upstream faults and the historical Turbopack/environment/dependency caveats remain as documented below; the earlier 4C-2 evidence is preserved, not relabelled as rerun in 5A-1
- Confirmed Places are client-submitted snapshots, not an independent authenticity guarantee or POI-detail validation. They are only echoed requirements: no scheduling, candidate recommendation pool, automatic transport, budget recalculation, persistence or 5A-2 work
- Final checks retain the original five cache files byte-for-byte; no real credentials are present in the 16 deliverables, and built frontend assets do not contain the backend Web Service key or JS security code. No environment/configuration or dependency files changed. Nothing staged, committed or pushed

### Actual changed files (16; excludes five preserved pre-existing caches)

- Backend modified: `backend/app/schemas/place.py`, `backend/app/schemas/trip.py`, `backend/app/api/trips.py`
- Backend test added: `backend/tests/test_trip_places.py`
- Frontend types/API modified: `frontend/src/types/place.ts`, `frontend/src/types/trip.ts`, `frontend/src/lib/places-api.ts`, `frontend/src/lib/api.ts`
- Frontend UI modified: `frontend/src/components/trip-request-form.tsx`, `frontend/src/components/trip-plan-result.tsx`
- Frontend UI added: `frontend/src/components/places/place-details.tsx`, `frontend/src/components/places/requirement-place-picker.tsx`, `frontend/src/components/trip/confirmed-places.tsx`
- Frontend tests added: `frontend/src/lib/api.test.ts`, `frontend/src/components/trip-request-form.test.tsx`
- Documentation updated: `docs/PROGRESS.md`

## Milestone 4C-2 Actual Implementation and Acceptance (2026-10-05, latest)

- Subsequent closeout: committed/pushed as `cb1a2605af312b9c8dcb319358615691ca56b0be` before the explicitly authorized 5A-1 task. The no-commit/push statements below describe the historical implementation/acceptance handoff; its evidence and limitations remain preserved

### Baseline, scope and data flow

- Read root/frontend AGENTS, PROJECT_CONTEXT, this document and applicable local Next.js instructions; checked the actual worktree against `84f0eb1`. Preserved the five pre-existing Python cache changes, without staging or committing any file
- Reused the existing Web Service key/configuration, HTTPX client/timeout and safe errors, strict route endpoints, 4A real POI search, 4B confirmed/day-scoped bindings, 4C-1 walking API and existing AMap instance/markers. No dependency, environment/configuration, lockfile, CORS or SDK proxy allowlist changes
- Data flow: original adjacent activities → explicitly confirmed Place bindings → user selects a mode and clicks query → `POST /routes/transit` → fixed `https://restapi.amap.com/v3/direction/transit/integrated` → normalized Pydantic response → frontend runtime validation → ordered steps and existing map overlays. No automatic routing when switching mode, no skipping unbound middle activities
- Request: `{ origin: { place_id, longitude, latitude }, destination: { place_id, longitude, latitude } }`, reusing the strict walking endpoint validator. Extra city/key/upstream URL fields are rejected; Shanghai is fixed on the server. Same POI/equal normalized coordinates avoid an upstream call
- Checked the [official directions documentation](https://lbs.amap.com/api/webservice/guide/api/direction). Use v3 default query conditions with `extensions=all`; do not send a Mock departure time, trip date or optimization strategy. UI explicitly says operating schedules for the travel date have not been verified
- Response: `{ status: "ok" | "no_route" | "unsupported" | "same_place", source: "amap", queried_at, selection_rule: "first_supported_complete", route }`. A successful route contains `duration_seconds`, `walking_distance_meters`, nullable `fare_cny`, `geometry_complete`, and ordered `legs`. Each leg has walking/bus/subway mode, nullable distance/duration, instruction, ride line and departure/arrival stop names, separate real geometry parts and completeness flag

### Selection, validation and implementation choices

- Iterate proposals in upstream order and select the first complete, supported, valid proposal. Never combine pieces from different proposals or claim the fastest/optimal scheme. Inside one ride segment, `buslines` are alternatives: take the first valid supported line with its own stops/geometry, not every line as a transfer
- City buses and subway types are explicitly supported. Required taxi, long-distance railway, unknown nonempty segment or unsupported ride type rejects the entire candidate. A walking-only result is not a transit scheme. Empty proposal list returns `no_route`; only unsupported candidates return `unsupported`; malformed available candidates with no valid fallback return a safe data error
- Real integration found that AMap emits `railway: { via_stops: [], alters: [], spaces: [] }` even in ordinary metro/bus segments. Added a narrowly validated empty-placeholder exception plus regression tests. Actual railway information, unknown fields and malformed nested placeholders remain rejected; no general recursive relaxation
- Use only proposal `duration` as total seconds and `walking_distance` as total access walking meters. Do not sum access time into the total again or label top-level `route.distance` as transit mileage. Fare missing is `null`/“未知”, never zero; invalid/non-finite/negative values fail. No per-person multiplication or Mock budget mutation. Nullable/zero optional leg metrics are allowed; the walking-only 100km limit is not reused
- Supplied geometry must contain bounded finite `[longitude, latitude]` points and be drawable. Missing geometry is allowed for an otherwise complete textual scheme but marks the leg/route incomplete. Each leg with known geometry gets an overlay preserving separate geometry parts; gaps are never bridged. Unknown/degenerate supplied geometry is conservatively rejected rather than invented
- Missing configuration 503, timeout 504, other upstream/business/data error 502, invalid input 422. Fixed upstream, TLS and redirects restrictions remain. At most two attempts, only retrying connection failure/connection timeout, not read timeout/business/data errors. Error bodies/logs do not reflect raw upstream errors or credential-bearing URLs
- Mode isolation: `trip-plan-result.tsx` synchronously invalidates **both** hooks before mode/day/edit changes and affected endpoint commits. `use-transit-route.ts` uses an AbortController plus monotonically increasing request version; both success and catch paths check version/signal. Separate mode hooks plus invalidation prevent walking→transit→walking and endpoint A→B→A old successes/errors from reappearing. Unmount aborts; a late response cannot steal a newer candidate-search view
- Map uses the existing instance and independent polylines: access walking blue dashed, bus purple solid, subway pink solid. One selected scheme only; switch/replace/hide/unmount removes old overlays. Route fit uses all known parts, marker fit is separate. Partial maps have a warning and “查看已知路段”, not a full-route claim

### Automated checks — offline Mock HTTP/SDK

| Command | Result |
| --- | --- |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -v` | Passed: **119/119** (78 existing + 41 new, including the real empty-railway shape regression) |
| `cd frontend && npm run test` | Passed: **13 files / 244 tests** (164 existing + 80 new: 48 API, 26 interaction, 6 map) |
| `cd frontend && npm run lint` | Passed |
| `cd frontend && npm run typecheck` | Passed: Next type generation + `tsc --noEmit` |
| `cd frontend && npm run build -- --webpack` | Passed: optimized production compilation, TypeScript, static generation and build traces; used this build in Chrome |
| `git diff --check` | Passed |

- Backend tests cover access walks, transfers, alternative-line consistency, candidate fallback, missing fare, malformed metrics/geometry, empty/unsupported plans, actual empty railway placeholders, same endpoints, safe validation/errors/key reflection, timeout and bounded retry. Existing 4A/4B/4C-1 coverage retained
- Frontend tests cover explicit query/no automatic mode request, missing/same endpoints, original adjacency, steps/units/unknown fare/no double counting or multiplying by travelers, Mock immutability, segmented map geometry/style/cleanup, Marker regression and candidate separation. Old successes **and errors** are tested across mode ABA, endpoint ABA, day switch and edit/regeneration; unmount explicitly covers abort/late-success cleanup
- These tests consume no real AMap quota. Live fault/empty/unsupported/missing-geometry and adversarial timing were not manufactured in Chrome. Existing backend Starlette/httpx deprecation warning remains; no separate backend lint/typecheck command is configured
- Used the requested known-good Webpack path, including network permission for the pre-existing Google Fonts setup. Historical Turbopack internal-port restriction is retained, not retried or claimed fixed

### Real HTTP and actual Chrome acceptance — not Mock tests

- Restarted only previous agent-owned frontend/backend sessions, using FastAPI at `http://127.0.0.1:8000` and the Webpack production frontend at `http://localhost:3000`. FastAPI started cleanly. No user-owned process was stopped, no new browser tooling/configuration installed
- The native Chrome connection initially produced gray screenshots/unresponsive date controls. After the user confirmed visibility and the window became responsive, actual form, search, binding, query, map and narrow-screen interactions were completed. No gray screenshot or code/API-only check is counted as browser acceptance
- Independent real HTTP: `/places/search` for 静安寺 and 东方明珠 returned 200; selected coordinates came from these real results. Initial transit response exposed the empty-railway compatibility issue above. After the targeted fix/retest/restart, `/routes/transit` returned 200/`ok`, a walking→01路→walking scheme; no Mock or hardcoded fixture was substituted
- Actual Chrome: submitted October 10–11 2026, budget 3000, two travelers, accommodation 静安寺附近, balanced pace. Explicitly searched/selected/confirmed adjacent Day 1 activity 1 → 静安寺（南京西路1686号）, activity 2 → 东方明珠广播电视塔. User-selected real POIs are independent of the original Mock activity names
- Inspected Chrome Network on the explicit first transit query: `POST /routes/transit` **200**, preflight **200**; normalized `duration_seconds=4331`, `walking_distance_meters=2144`, `fare_cny=2`, `geometry_complete=true`, source/query time and `first_supported_complete`. UI correctly displayed **预计73分钟、接驳步行2.14公里、参考票价¥2.00**. The scheme used **01路（上海西站--蓝村路南泉路）**, boarding **延安西路华山路**, alighting **世纪大道浦东南路**. Later queries can differ slightly in upstream duration
- Screenshots are local-only in `/private/tmp/trippilot-4c2-evidence.UGQFZM/`; page screenshots and Network filtered to `/routes/transit`, no HAR/raw network log or environment/credential values saved to the repo

| Test | Result | Actual observation / evidence |
| --- | --- | --- |
| Form, real searches and explicit confirmed bindings | Passed | Actual form reached Mock result; 静安寺 and 东方明珠 returned real names/addresses/markers; no default automatic binding |
| Mode switch does not query | Passed | Walking→transit showed idle query button without scheme; transit→walking→transit cleared old values/legend, requiring new explicit clicks. `05-mode-clears-transit.png` |
| Real transit values, ride and stops | Passed | 200 response matched 73min / 2.14km / ¥2 and the 01路 stations, with ordered access/ride/egress steps. `01-real-network.png`, `02-transit-steps.png` |
| Real segmented map and route-fit | Passed | Visible AMap roads/labels/attribution, purple ride and blue dashed walks, both real endpoint markers; “查看公交方案全貌” restored full scheme viewport. `03-transit-map-full.png` |
| Marker ↔ activity while route visible | Passed; reverse direction rechecked 2026-10-06 | Earlier marker 1 click visibly focused/highlighted 外滩漫步. The earlier `04-activity-marker-link.png` remains insufficient evidence of reverse centering. The focused recheck below clicked each activity's “在地图查看”, waited for animation, and captured its centered/highlighted Marker plus restored full-route views; previous selection returned to normal and no route request was added |
| Walking regression | Passed | Explicit walking query after mode switch produced **8.67km / 预计116min** with blue walking geometry and full-route control. `06-walking-regression.png` |
| Day isolation | Passed | Day 2 removed scheme/markers and showed missing endpoints; return to Day 1 retained both bindings without restoring old route. `09-day-switch-clears.png` |
| Replace, cancel/reenter, candidate separation | Passed | Replacing activity 2 hid itinerary route from candidate view. Cancel kept original binding; reenter reset keyword/candidates/selection and disabled confirmation. Explicitly searched 东方明珠 again, selected 旅游码头 and confirmed |
| Endpoint replacement cleanup/requery | Passed | Confirming 东方明珠旅游码头 immediately cleared old total/steps/line. New explicit query returned **预计72min / 2.11km / ¥2** for the changed endpoint. `10-endpoint-change-clears.png` captures cleared state |
| Unbind cleanup | Passed | Unbound activity 2 after the new successful query: both adjacent controls disabled/missing-endpoint, old route removed, only activity 1 marker retained. `11-unbind-clears.png` |
| Edit/regenerate | Passed | Form retained original request; resubmission created an unbound result, default walking mode, no prior scheme or candidates |
| Narrow layout | Passed, simulated | Chrome responsive **400×748**: controls/steps/long instructions wrap; map, legend and fit control usable without observed horizontal clipping. `07-narrow-steps.png`, `08-narrow-map.png`. Not a physical phone test |
| Mock remains separate | Passed | Activity time/transport/cost/weather still labelled Mock; estimated total ¥460, transport ¥40, remaining ¥2540 unchanged |

- Console inspected: two AMap Canvas2D `willReadFrequently` performance warnings, no observed application exception at that point; DevTools showed an additional Issue, not diagnosed/claimed fixed. This is not a guarantee of an entirely clean browser log or external network reliability

### Focused activity → Marker recheck (2026-10-06, Asia/Shanghai)

- Scope: only the previously incomplete reverse-link visual evidence. Reused the running frontend on `localhost:3000` and backend on `127.0.0.1:8000`; no restart or service termination. The browser-loaded `page-b4ff3e980b973224.js` matched the current local Webpack build byte-for-byte; that build postdates the current frontend source changes
- Native computer control could not connect (`native pipe startup failed`). Used the already available browser-control tool connected to real Chrome 154 instead; no new tool installation, project dependency, application mock, or browser configuration change. The screenshot helper initially timed out; bringing the page to the foreground and allowing a longer capture timeout produced the verified screenshots below. Failed captures are not counted as evidence
- Submitted a fresh two-day result, explicitly searched and confirmed Day 1 外滩漫步 → 静安寺（南京西路1686号）and the originally adjacent 午餐与休息 → 东方明珠广播电视塔（世纪大道1号）. Both real `GET /places/search` calls returned 200. Switched to transit, then explicitly queried once: `POST /routes/transit` returned 200. This later live response displayed **预计124分钟 / 接驳步行1.28公里 / 参考票价¥6.00**, with blue dashed walking and purple bus segments; values were not substituted from the earlier 01路 sample
- Evidence directory: `/private/tmp/trippilot-4c2-marker-review.uKBu9i/`. Each PNG was opened and visually checked: the full 454×384 map canvas, target Marker, map attribution and route controls are visible within the 1440×1100 page screenshot. No credentials or raw network logs are included in the shared evidence

| Action / check | Result | Actual observation / screenshot |
| --- | --- | --- |
| Explicit transit query, then full scheme view | Passed | Real roads/labels, blue walking and purple bus geometry plus both endpoint markers visible. `01-transit-full-before.png` |
| Click 外滩漫步 → “在地图查看” | Passed | Waited 3 seconds; 静安寺 Marker **1** centered and red/orange at 1.2× scale. Rendered DOM center offset rounded to **0px / 0px**; Marker 2 returned to green/1×. `02-activity-1-centered.png` shows the complete map and centered target; `03-activity-1-full-route.png` shows both markers, with only 1 highlighted, and retained full geometry |
| Click 午餐与休息 → “在地图查看” | Passed | Waited 3 seconds; 东方明珠 Marker **2** centered and red/orange at 1.2× scale, center offset **0px / 0px**; Marker 1 returned to green/1×. `04-activity-2-centered.png` shows the complete map, centered target and local walking/bus geometry |
| Click “查看公交方案全貌” after the second activity action | Passed | Waited 3 seconds; all scheme geometry and both endpoints visible again, Marker 2 highlighted and Marker 1 normal. `05-activity-2-full-route.png` |
| Retained result / no implicit requery | Passed | The same 124min / 1.28km / ¥6 result and steps remained throughout. Browser Network filtered to `/routes/(transit\|walking)` showed exactly **one POST /routes/transit → 200** after the query, after each activity click and after the final fit operation; zero walking requests |

- Console error-level check returned **0 errors**; one warning remained, not claimed resolved. No application defect observed in this focused check. Only `docs/PROGRESS.md` changed this round; no dependency/source/test changes, no repeat of the full automated suite. `git diff --check` passed; the five original Python cache files remain unchanged. No commit/push
- The later response included several bus rides, but this round checked activity/Marker selection and geometry retention only. It does not constitute a separate subway/transfer-sample acceptance campaign or real-fault test; the limitations below remain

### Limits and handoff

- Focused real **bus** scheme acceptance passed. Live subway, multi-transfer, missing-fare and partial-geometry variants were not separately forced; their parsing/rendering is covered by controlled automated tests, not claimed live samples. No guarantee of fastest route, future-date operations, realtime arrivals or real ticket pricing
- The formerly pending activity→Marker evidence is closed by the 2026-10-06 focused recheck above. The earlier final recapture interrupted by Chrome window changes and its incomplete search/rebinding attempt remain historical control interruptions, not successful acceptance or evidence of an application failure
- Physical phone/touch, broader browser/device matrix and real upstream fault behavior remain pending/separate. Historical network intermittency, Turbopack restriction, font download requirement, dependency advisories and backend environment warning remain below
- Deliberately no persistent route cache/history, automatic routing, multi-scheme comparison, scheduling, budget rewrite, LLM or other stage. Public transit uses its own contract/hook without replacing the accepted walking implementation or introducing a new architecture dependency
- Final exact-value secret check: all 16 deliverables contain none of the three locally configured AMap credentials; 31 frontend static assets contain neither the backend Web Service key nor the JS security code. Values were not printed. SHA-256 hashes of all five pre-existing Python caches are unchanged; staging remains empty
- Services remain running for review at the above local addresses. No commit/push; stop at 4C-2

### Actual changed files (16; excludes five preserved pre-existing caches)

- Backend added: `backend/app/schemas/transit.py`, `backend/app/integrations/amap_transit.py`, `backend/tests/test_transit.py`
- Backend modified: `backend/app/api/routes.py`
- Frontend added: `frontend/src/types/transit.ts`, `frontend/src/lib/transit-api.ts`, `frontend/src/lib/use-transit-route.ts`, `frontend/src/components/trip/transit-route-segment.tsx`
- Frontend modified: `frontend/src/components/trip-plan-result.tsx`, `frontend/src/components/trip/day-timeline.tsx`, `frontend/src/components/places/place-explorer.tsx`, `frontend/src/components/places/place-map.tsx`
- Frontend tests added: `frontend/src/lib/transit-api.test.ts`, `frontend/src/components/trip/transit-route.test.tsx`, `frontend/src/components/places/place-map-transit.test.tsx`
- Documentation updated: `docs/PROGRESS.md`

## Milestone 4C-1 Actual Implementation and Acceptance (2026-10-05)

- Subsequent closeout: committed/pushed as `84f0eb17f618a13b01e5e16f4b906c32ac1c5e9b` before the explicitly authorized 4C-2 task. Implementation-time no-commit/next-stage statements below are historical; previous acceptance limitations remain applicable

### Baseline, scope and data contract

- Re-read root/frontend instructions, PROJECT_CONTEXT and this progress file; inspected the actual checkout, existing APIs, maps, tests and startup commands. Implementation started from the accepted 4B baseline `0eeba167766216ffbaecfa4610c83539ae36577d`. Preserved the five pre-existing tracked/untracked Python cache changes; no full staging, commit or push occurred during implementation acceptance
- Reused 4A POI search, the configured HTTPX client/Web Service key/safe logging, 4B confirmed bindings and daily map/marker interactions. No dependency, environment variable, lockfile, proxy allowlist, CORS origin or original Mock TripPlan change
- Checked the current [official Web Service directions documentation](https://lbs.amap.com/api/webservice/guide/api/direction): fixed GET `https://restapi.amap.com/v3/direction/walking`, longitude then latitude (up to six decimals), distance in meters and duration in seconds. The existing SDK proxy is not used or widened for walking
- New business endpoint: `POST /routes/walking`, body `{ origin: { place_id, longitude, latitude }, destination: { place_id, longitude, latitude } }`. Reject extra fields, blank/invalid POI IDs, strings/booleans/non-finite or out-of-range coordinates; safe 422 validation does not echo user input. The client cannot supply an upstream URL or key
- Response: `{ status: "ok" | "no_route" | "same_place", source: "amap", queried_at, route }`. `route` is null for no-route/same-place, otherwise `{ distance_meters, duration_seconds, segments }`; each segment is an array of `[longitude, latitude]` points from one upstream step. Validate positive finite metrics, the documented 100km walking limit and drawable bounded geometry; malformed responses return safe 502, not fabricated routes
- Missing server configuration returns 503; upstream timeout returns 504; other HTTP/business/data failures return 502. Reuse existing timeout/TLS behavior and at most one retry only for connection errors/timeouts; no retry for read timeout, business failure or malformed data. Logs contain exception type/attempt only, never full credential-bearing URLs or raw upstream errors

### Frontend behavior and implementation choices

- Every adjacent pair is derived from `day.activities[index + 1]`; the result handler checks the same original adjacency again. An unbound middle activity never creates an A→C shortcut. Query buttons are explicit; missing endpoints prompt binding, same POI/equal coordinates show same-place state without a meaningless request
- Loading, success, no route, same place, timeout and other failures have separate UI states. Successful distance/time is formatted from normalized units, with “预计”, AMap source and query time. Original Mock transport, activity time, weather and budget remain separate and unchanged
- `use-walking-route.ts` owns one selected query. Every cancellation/query/invalidation increments a monotonic request version and aborts the previous controller; responses and failures must still match the version and a live signal before updating state. This protects even A→B→A and transports that ignore abort
- `trip-plan-result.tsx` invalidates synchronously before committing replacement/removal; day switch and edit invalidate all. Unmount aborts via the hook cleanup. Cancelled replacement does not invalidate a still-valid route; a late success cannot switch a newer candidate-search view back to itinerary
- `place-map.tsx` adds one AMap Polyline to the existing map; paths retain upstream step boundaries, so gaps are not joined with invented straight lines. Replacement, hiding in candidate view and unmount remove the old overlay. Full-route fit uses the polyline bounds independently of marker fit-all; markers keep their existing selection/centering behavior and remain above the route
- Deliberate limits: one selected route, first upstream proposal, no route cache/history and no automatic query; returning to a day requires an explicit new query. No schedule/budget optimization or persistence. “Same place” is based on POI identity/coordinates, not matching display names

### Automated verification — Mock upstream/SDK, not live acceptance

| Command | Result |
| --- | --- |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -v` | Passed: 78/78 (53 existing + 25 new). Existing Starlette/httpx deprecation warning remains |
| `cd frontend && npm run test` | Passed: 10 files / 164 tests (99 existing + 65 new: 41 API, 19 route interaction, 5 map overlay tests) |
| `cd frontend && npm run lint` | Passed |
| `cd frontend && npm run typecheck` | Passed: Next type generation + `tsc --noEmit` |
| `cd frontend && npm run build -- --webpack` | Passed: optimized production compilation, TypeScript, static generation and build traces; this production build was used in Chrome |
| `git diff --check` | Passed |

- Backend coverage: strict input/safe 422, longitude/latitude ordering and six-decimal serialization, meters/seconds, same-place no upstream call, empty paths, malformed geometry/metrics, timeout, bounded connection retry, upstream HTTP/business errors and secret-safe output/logs
- Frontend coverage: explicit query, missing/same endpoints, original adjacency without skipping, route replacement/cleanup, cancelled replacement, invalidation on both endpoints, endpoint A→B→A, cross-day late responses, search-view isolation, edit/regenerate/unmount, input/response validation and map overlay/fit cleanup. All existing 4A/4B tests remain passing
- Automated tests mock AMap HTTP and the map SDK and consume no real quota. Adversarial late responses, empty routes, malformed data and upstream failures were verified here, not deliberately induced against live AMap
- Did not retry the default Turbopack build: used the already-verified webpack fallback as requested. The historical internal-port permission limitation below is retained, not claimed newly fixed. No lint/typecheck scripts exist for the backend; its available unittest suite is the recorded backend check

### Actual Chrome acceptance — real service, separately verified

- Used native Chrome controls against the actual production frontend `http://localhost:3000` and FastAPI `http://127.0.0.1:8000`. Restarted only the previous agent-owned sessions to load this implementation; no user-owned service was stopped. FastAPI startup was clean, `/health` returned 200 and OpenAPI contained the new endpoint
- Submitted October 10–11, 2026, budget 3000, 2 travelers, accommodation near Jing'an Temple, balanced pace. Actual POST /trips/plan returned the existing Mock result. Explicitly searched and confirmed Day 1 activity 1 → 武康大楼 (淮海中路1850号), activity 2 → 静安寺 (南京西路1686号)
- Made four explicitly triggered real POI searches (an initial activity-name search, 武康大楼, 静安寺, and 武康大楼 again for replacement) and three explicitly triggered walking queries; no batch/stress requests. All three queries produced real route UI/geometry. The first walking POST was inspected in Chrome Network: 200 OK, 93ms; its preflight also returned 200
- The first response contained `distance_meters: 2691`, `duration_seconds: 2153`, `status: "ok"`, `source: "amap"`, query time and coordinate segments. UI showed **2.69 公里 · 预计 36 分钟** (2691 / 1000 rounded to two decimals; ceil(2153 / 60)). The observed polyline follows road bends between the selected POIs, not a straight-line placeholder
- Screenshots are local-only in `/private/tmp/trippilot-4c1-acceptance.7W66aF/`. Only page content and Network filtered to the local walking endpoint were saved; no HAR, .env data, credentials or unredacted SDK URLs were saved/shared

| Test | Result | Actual observation / screenshot |
| --- | --- | --- |
| Form → result, real POI binding and explicit walking request | Passed | Confirmed both adjacent activities, then clicked query. Network response metrics match the UI; original Mock traffic remains alongside it. `01-route-response.png` |
| Real map geometry | Passed | AMap roads/place labels/attribution and blue road-following polyline visibly rendered between 武康大楼 and 静安寺. `02-real-walking-map.png` |
| Activity ↔ Marker while route visible | Passed | Clicked real marker 1: 外滩漫步 gained focus/highlight; clicked 午餐与休息 title: marker 2 centered/highlighted and route remained. `03-marker-link-with-route.png` captures the latter |
| View full walking route | Passed | After activity selection zoomed into 静安寺, the full-route button restored a viewport containing both endpoints and the full path. `04-full-route-fit.png` |
| Candidate/route separation | Passed | Switched to candidate search: no bound markers or blue line. Returning to daily bound view restored the still-valid route. `05-candidate-view-no-route.png` |
| Cancel replacement / reopen | Passed | Cancelled activity 2 replacement: original 静安寺 binding and route retained. Reopening prefilled the activity name, showed no old candidate selection and disabled confirmation. `06-cancel-keeps-binding-route.png` |
| Confirm replacement clears old route | Passed | Searched and confirmed 武康大楼主题邮局 for activity 2: old 2.69km/36min and old line disappeared immediately. No automatic query. `07-replacement-clears-route.png` |
| Query new endpoints | Passed | A new explicit query displayed 123米 / 预计2分钟 and the new short road-following path. `08-new-route-after-replacement.png` |
| Day switch invalidation / binding retention | Passed | Switching to Day 2 removed the line and bound markers; returning to Day 1 retained both bindings but did not restore the old route. Only another explicit query restored 123米 / 预计2分钟. `09-day-switch-clears-route.png` |
| Narrow viewport | Passed | Chrome responsive 400×748: route card and action controls wrap correctly; “在地图查看路线” scrolls to a real map with both endpoints, blue line, full-route control and attribution. `10-narrow-route-card.png`, `11-narrow-route-map.png` |
| Unbind endpoint clears route | Passed | After the third successful query, unbound activity 2. Both affected pair controls show missing-endpoint/disabled state; old metric and line disappear, activity 1 and its marker remain. `12-unbind-clears-route.png` |
| Mock data unchanged | Passed | Existing activity times, transport/costs and weather remain explicitly Mock. Estimated total stayed ¥460, transport ¥40, remaining budget ¥2540; real walking query never rewrote them. Visible throughout screenshots |

- Console inspection: three AMap Canvas2D `willReadFrequently` performance warnings, no observed application exception. DevTools also showed two Issues, not diagnosed or declared fixed in this scope. No claim of a completely clean browser console or permanent external network reliability

### Remaining items and handoff

- No observed blocking 4C-1 issue in the focused actual Chrome acceptance. Physical-phone/touch and broader browser/device matrix remain **manual acceptance pending**; responsive Chrome is not a physical-device test
- Live upstream fault/empty-route and adversarial race scenarios were intentionally not manufactured. Their automated Mock results do not constitute real-fault acceptance. External AMap connectivity remains dependent on the user's network and service quota; historical 4A intermittency notes remain applicable
- Default Turbopack environment restriction, existing dependency advisories and Python/Starlette environment warnings remain separate maintenance items. No credentials/proxy security limits were changed to bypass them
- Exact-value review found no locally configured credentials in the 17 code/test deliverables; 31 built frontend static assets contained neither the backend Web Service key nor the JS security code. No secret values were printed
- At implementation handoff, services remained available on localhost:3000 (webpack production build) and 127.0.0.1:8000; no commit/push had occurred. Subsequent review authorizes normal commit/push of only the 18 listed deliverables, excluding the five existing caches. Acceptance limitations above remain unchanged; no next-stage work is authorized

### Actual changed files (18; excludes five preserved pre-existing caches)

- Backend added: `backend/app/schemas/route.py`, `backend/app/integrations/amap_walking.py`, `backend/app/api/routes.py`, `backend/tests/test_routes.py`
- Backend modified: `backend/main.py` (register walking router)
- Frontend added: `frontend/src/types/route.ts`, `frontend/src/lib/routes-api.ts`, `frontend/src/lib/use-walking-route.ts`, `frontend/src/components/trip/walking-route-segment.tsx`
- Frontend modified: `frontend/src/components/trip-plan-result.tsx`, `frontend/src/components/trip/day-timeline.tsx`, `frontend/src/components/places/place-explorer.tsx`, `frontend/src/components/places/place-map.tsx`, `frontend/src/lib/amap-loader.ts`
- Frontend tests added: `frontend/src/lib/routes-api.test.ts`, `frontend/src/components/trip/walking-route.test.tsx`, `frontend/src/components/places/place-map-route.test.tsx`
- Documentation updated: `docs/PROGRESS.md`

## Milestone 4B Actual Implementation and Acceptance (2026-10-04)

- Subsequent closeout: committed and pushed as `0eeba167766216ffbaecfa4610c83539ae36577d` before 4C-1. The implementation-time no-commit statement below is historical; the accepted baseline is now recorded above. Turbopack, physical-phone and live-fault limitations are retained

### Scope and implementation

- Re-read root/frontend instructions, PROJECT_CONTEXT and this progress file, inspected the actual checkout and the supplied temporary patch before applying adapted changes. The checkout was still 4A; pre-existing dirty files were only Python bytecode caches and were preserved
- Reused the patch's local binding design, not its temporary/unlanded progress claims or its 87-test result. Added explicit “确认绑定”, visible “来源：高德地图”, an accessible activity-title map button and actual TripPlanner lifecycle/stale-response tests
- Activity actions: bind, view in map, replace and unbind. Search is user-triggered with the activity name prefilled; selecting a candidate or marker does not bind. Cancelling, failure and empty results preserve an existing binding
- Separate “搜索候选地点” and “当天已绑定地点” views; daily activity ↔ marker selection/centering/highlighting, fit-all and shared-POI deduplication. Identity includes day/date/activity ID, not activity name. Original Mock plan data is not edited
- Bindings live only inside the mounted result. Edit/regenerate/refresh clears them, with a visible notice. New session identifiers plus abort/request ID/context isolation handle cancel/reopen, A→B→A, day changes and late responses across regeneration
- No new dependency, API, backend business change, environment change, proxy permission, storage, route planning or real weather was introduced

### Automated checks — actual checkout, not real AMap acceptance

| Command | Result |
| --- | --- |
| `cd frontend && npm run test` | Passed: 7 files / 99 tests, including all existing 76 tests and 23 new tests |
| `cd frontend && npm run lint` | Passed |
| `cd frontend && npm run typecheck` | Passed: Next type generation + `tsc --noEmit` |
| `cd frontend && npm run build` | Attempted twice, including an approved retry; blocked by Turbopack CSS compilation trying to bind an internal port: `Operation not permitted (os error 1)` |
| `cd frontend && npm run build -- --webpack` | Passed: optimized production compilation, TypeScript, static generation and build traces |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -v` | Passed: all 53 existing tests; existing Starlette/httpx deprecation warning remains |
| `git diff --check` | Passed |

- Automated tests mock fetch and the SDK and use the repository's existing test framework; they do not consume AMap quota. Cover explicit confirmation, replace/remove/cancel/error/empty, same-activity reopening, A→B→A and late responses, cross-day isolation, shared-POI removal/remaining-marker selection, map deduplication, immutable Mock data, and actual Planner edit/regenerate/unmount lifecycles
- One new test initially reused an already-consumed Response fixture; corrected the fixture to return a fresh Response. Final full-suite reruns passed with no product test failures

### Real Chrome acceptance — passed, separately from Mock tests

- Reused the existing FastAPI on `127.0.0.1:8000`; `/health` returned 200. Stopped only the agent-owned frontend dev session to avoid concurrent `.next` writes during build, then served the successful production build at `http://localhost:3000` using `npm run start -- --hostname localhost`. Backend was not restarted or stopped
- Used native Chrome controls on a dedicated local tab, not a Mock browser/map or Playwright. Submitted October 10–11, 2026, budget 3000, 2 travelers, accommodation near Jing'an Temple and balanced pace; entered the actual backend Mock result
- Made six explicitly triggered real POI searches total for the following acceptance sequence, not batch/stress requests. The two different keywords were 武康大楼 and 静安寺. Network panel evidence shows each keyword's `GET /places/search` returned 200 (206 ms / 223 ms) with 20 real candidates; subsequent searches also displayed real candidates successfully
- Screenshot directory is local-only: `/private/tmp/trippilot-4b-acceptance.xBg82r/`. Only page screenshots and Network filtered to the local POI path were saved; no HAR, .env contents, credentials or unredacted SDK URLs were saved/shared. The files below are the evidence index

| Test | Result | Actual observation / screenshot |
| --- | --- | --- |
| Real map and two real keywords | Passed | Roads, place labels, AMap copyright and numbered candidate markers visible. 武康大楼: 淮海中路1850号; 静安寺: 南京西路1686号. `01-real-search-network.png`, `03-jingan-search-network.png` |
| Preview vs explicit confirmation | Passed | Selecting 武康大楼 highlighted candidate/marker while the activity remained unbound; only “确认绑定” saved it and switched to daily bound view. `02-preview-not-bound.png` |
| Activity → marker | Passed | Clicked the bound 外滩漫步 title; the 武康大楼 marker centered and turned red, with other daily POI outside that zoomed view. `04-activity-to-marker.png` |
| Marker → activity | Passed | In daily fit-all, clicked green 静安寺 marker 2; 午餐与休息 gained focus/highlight, scrolled into view, and marker 2 became red/centered. `06-marker-to-activity.png` |
| Daily fit-all | Passed | “查看当天全部地点” showed both 武康大楼 and 静安寺, without a route line. `05-day-fit-all.png` |
| Day isolation/preservation | Passed | Day 2 initially had no bindings. Bound its same-name 午餐与休息 to 武康大楼; returning to Day 1 retained its 午餐与休息 → 静安寺 and both original Day 1 bindings. Day 2 remained bound after Day 1 replacement/unbinding. `07-day2-independent-binding.png`, `08-day1-binding-preserved.png` |
| Cancel/reopen and A→B→A | Passed | Previewed 静安寺 as a replacement for 外滩漫步, cancelled, reopened that activity and switched A→B→A. Original bindings stayed intact; search returned to the activity-name prefill, old candidates/markers were absent and confirm was disabled. `09-cancel-reopen-cleared.png` |
| Confirm replacement/shared POI | Passed | Searched again and explicitly replaced 外滩漫步 with 静安寺. Two Day 1 activities bound to that POI produced exactly one marker. `10-replace-shared-poi.png` |
| Partial unbinding | Passed | Unbound 外滩漫步; 午餐与休息 and its one marker remained. Clicking the remaining marker focused/highlighted 午餐与休息. `11-unbind-one-keeps-shared-poi.png` |
| Narrow screen | Passed | Chrome responsive 400×748: single-column cards, source/address, action buttons, map switches, fit-all, 320px-high real map and attribution visible without obvious overflow/overlap. Desktop activity→map button also worked in the narrow viewport. `12-mobile-activity.png`, `13-mobile-map.png` |
| Edit/regenerate clears binding | Passed | Edit preserved dates/budget/travelers/accommodation. Resubmission yielded no old binding on either day, no bound markers and disabled daily fit-all. `14-regenerated-bindings-cleared.png` |
| Refresh clears result/binding | Passed | Bound Day 2 again, then refreshed Chrome. Returned to a fresh empty request form with no restored result/binding. `15-refresh-clears-result.png` |
| Mock/real distinction | Passed | Real POI/source and map remain separate from clearly labeled Mock activity times, traffic, costs and weather; the overview cost stayed 460 and no route/real traffic claim appeared. Visible throughout activity/map evidence above |

- Console inspection showed four AMap Canvas2D `willReadFrequently` performance warnings, no observed application exception. The captured POI requests succeeded; no raw SDK URL or network export is included. The historical 4A unallowlisted SDK telemetry route remains outside proxy scope and was not enabled

### Remaining issues / manual acceptance

- No observed blocking 4B functional issue in this focused Chrome run. Default Turbopack build is still environment-blocked; the successful webpack production build was the one actually served/tested
- Physical phone/touch and browser/device matrix testing remain **manual acceptance pending**; responsive Chrome is not a physical-device test
- Failure/empty-result and adversarial late-response sequences passed automated Mock tests. They were not deliberately induced against the live AMap service, so they are not claimed as real-network acceptance
- Real AMap connectivity can still be intermittent, as documented in 4A. No backend/network/proxy broadening was necessary in this 4B run
- Existing dependency advisories and Python/Starlette environment notes remain separate maintenance items, not remediated in 4B
- Services left available: production frontend on localhost:3000 and the existing backend on 127.0.0.1:8000. To resume HMR later, stop the production frontend and use `npm run dev -- --webpack`
- No commit/push performed; work stops after 4B

### Actual changed files (11; excludes preserved pre-existing caches)

- Modified: `frontend/src/components/trip-plan-result.tsx`, `frontend/src/components/trip/activity-card.tsx`, `frontend/src/components/trip/day-timeline.tsx`, `frontend/src/components/places/place-explorer.tsx`, `frontend/src/components/places/place-map.tsx`, `frontend/src/components/places/place-search.tsx`
- Added: `frontend/src/lib/activity-places.ts`, `frontend/src/lib/activity-places.test.ts`, `frontend/src/components/trip-plan-result.test.tsx`, `frontend/src/components/trip/trip-planner.test.tsx`
- Updated: `docs/PROGRESS.md`
- Backend source/config/tests, `.env`/examples, package manifests and lockfiles unchanged. Existing tracked/untracked backend Python caches were preserved, not part of this implementation

## Milestone 4A Focused Connectivity and Browser Acceptance (2026-10-04, latest)

### Connection diagnosis and minimal repair

- Re-read project instructions/context/progress and inspected the actual POI HTTP client, map/search components and startup configuration. Ports 3000/8000 were unused, so started the existing FastAPI and Next.js webpack development commands for this test; no user process was stopped or new dependency installed
- HTTPX's existing default client picks up local macOS system proxies even without HTTP(S)_PROXY environment variables. The configured local proxy port was reachable. No-key HTTPS checks succeeded both through the system-proxy channel and with environment proxy discovery disabled, with TLS verification retained
- A real page search for 武康大楼 succeeded, but the next 静安寺 page request returned 504. Immediately comparing that same keyword through the system-proxy and direct channels returned HTTP 200 / AMap status=1 / 20 POIs on both. Together with the preceding run's ConnectTimeout evidence, this indicates intermittent upstream transport/connectivity failure, not a persistently invalid key or query. The exact external cause (DNS/local proxy/network/upstream) is **not proven**, and no OS proxy configuration was changed
- Added at most one retry (two attempts total) only for ConnectTimeout/ConnectError on the fixed, read-only POI GET. Read/write/pool timeouts, HTTP statuses, redirects, malformed responses and AMap business errors are not retried. Existing timeout values, TLS verification, system-proxy behavior, request parameters and safe 502/504 contracts remain intact
- Failure logs contain only a fixed message, exception class and attempt number; never the exception string, URL, key or response body. This makes any future timeout stage diagnosable without secret exposure. No automatic direct-channel fallback, blanket retry, dependency, configuration variable or JS-proxy change was introduced
- Restarted only this round's temporary backend to load the change. The final successful live requests did not demonstrate an actual retry; transient recovery through retry is verified by Mock tests, not falsely attributed to the two live successes

### Actual Chrome operation — the three requested items passed

- Used native Chrome browser controls, real AMap SDK/tiles and real FastAPI POI responses; no Mock map/POI or Playwright. Submitted a valid October 10–11, 2026 trip (2 travelers, budget 3000, accommodation near Jing'an Temple, balanced pace), received POST /trips/plan 200 and entered the result page
- Visually observed AMap roads, place labels and copyright, not just a container. The page continues to label real POI/map preview separately from Mock itinerary time/transport/cost/weather; choosing a POI did not modify TripPlan

| Test | Result | Actual observation / local screenshot evidence |
| --- | --- | --- |
| Two keywords through the repaired backend | Passed | Browser GET /places/search?keyword=武康大楼&city=上海 returned 200 in 285 ms; the following 静安寺 request returned 200 in 282 ms. Each produced 20 real candidates with names/addresses and numbered markers. `05-two-real-searches-http-200.png`, `06-jingan-new-results.png` |
| Marker → candidate card | Passed | Clicked 武康 result marker 17: 兴国花园 card became selected, marker turned red and moved to map center. Also clicked 静安 result marker 16: 静安寺枢纽站 card selected. `02-marker-to-card-17.png` |
| Candidate card → marker | Passed | Clicked 武康 card 18: its marker centered/turned red and marker 17 returned green. After the backend repair, clicked 静安 card 12: its marker centered/turned red, card 12 selected and card/marker 16 deselected. `03-card-to-marker-18.png`, `08-jingan-selection-detail.png` |
| Second search clears old markers | Passed | Search-start accessibility observation removed all 20 old numbered markers and old cards, clearing selection. Even the initial failed second search left no stale markers (`04-second-search-timeout-old-markers-cleared.png`). Final successful 静安 search had exactly 20 numbered marker elements, new candidates unselected by default and no 武康 candidate cards; map displayed the new area, not a combined 40-marker set. `06-jingan-new-results.png` |
| 查看全部搜索结果 | Passed | From card 12's centered view (some other points off-screen), clicked the real button. Map recentered to cover the new candidate extent, including outer markers 16/7/18; selected marker 12 stayed red and its card stayed selected. Nearby/identical coordinates can overlap visually; the control covers the positions, not artificially separated coordinates. `09-jingan-fit-all.png` |

- All screenshot filenames above are in `/tmp/trippilot-4a-acceptance-cujbgs/` (local temporary artifacts, not tracked repository files). Screenshots contain only page content or Network filtered to local /places/search, never credential-containing SDK URLs; no raw network logs/HAR were saved
- Inspected Console: the initial 504 was visible as a safe local resource failure; the final Network capture shows both POI requests at 200. SDK canvas/preload warnings and the previously documented restricted logging-route 404/ORB are not declared fixed; no claim of an entirely clean Console or permanent external-network reliability is made

### Automated verification — separate from the real browser checks

| Command | Result |
| --- | --- |
| `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests -v` | 53/53 passed, all prior 52 retained. Added both transient connection-recovery cases; expanded permanent-failure attempt limits, read/write/pool no-retry and sensitive-log protection |
| `cd frontend && npm run test` | 4 files / 76 tests passed; Mock fetch/SDK, no real API quota |
| `cd frontend && npm run lint` | Passed |
| `cd frontend && npm run typecheck` | Passed |
| `git diff --check` | Passed |

- Frontend business code was unchanged in this follow-up; production build was not rerun. The previous empty-map-configuration webpack build remains the recorded build result, not a new result for this round
- This round modified only `backend/app/integrations/amap.py`, `backend/tests/test_places.py` and `docs/PROGRESS.md`. Preserved all earlier uncommitted 4A work and generated cache changes; did not edit environment files or expose credentials
- Exact-value checks found no locally configured credentials in these three deliverable files. Final GET /health and frontend HTTP checks both returned 200. This round's test services are left running at `http://localhost:3000` and `http://127.0.0.1:8000` for the user to review; no user-owned process was stopped
- The three requested desktop acceptance items are no longer blocked. Remaining: physical phone/touch verification, broader rapid-query/unmount/day-switch real-browser cases, pre-existing dependency advisories and possible recurrence of external network interruptions. No 4B, route, weather API, commit or push

## Milestone 4A Browser Follow-up and Proxy Fix (2026-10-04)

### Cause and focused fix

- Actual Chrome acceptance exposed a mismatch missed by the earlier handcrafted HTTP probe: DistrictSearch sends duplicate key/s parameters plus platform, logversion, sdkversion, appname and csid. The proxy returned 400 before forwarding; the frontend consequently could not obtain Shanghai's center or create a map
- Normalize only valid key/s copies, validating every copy so conflicting values cannot be hidden by first/last-value semantics. All other duplicate parameters remain rejected
- Validate the five observed SDK metadata fields and remove them before forwarding. appname is restricted to the same two local origins as existing CORS; it is not an upstream target. No new route, dependency, credential exposure, CORS wildcard or TLS bypass was added
- Added six offline regression tests, including the real request shape with fictional credentials, duplicate conflicts/order, metadata variants and rejection, and no forwarding of metadata. The existing trip/POI tests remain intact
- This follow-up changed only `backend/app/integrations/amap_proxy.py`, `backend/tests/test_amap_proxy.py` and `docs/PROGRESS.md`; earlier uncommitted 4A work was preserved

### Automated verification — passed, not real-service acceptance

| Command | Result |
| --- | --- |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -v` | 52/52 passed; includes all original tests |
| `cd frontend && npm run lint` | Passed |
| `cd frontend && npm run test` | 4 files / 76 tests passed; mocked fetch/SDK |
| `cd frontend && npm run typecheck` | Passed |
| `cd frontend && NEXT_PUBLIC_AMAP_JS_KEY= NEXT_PUBLIC_AMAP_SERVICE_HOST= npm run build -- --webpack` | Passed, including static generation without map configuration; used the previously documented webpack fallback |
| `git diff --check` | Passed |

### Real Chrome verification — partial pass, with evidence

- Used native Chrome controls, not Playwright or mocked map/POI data. Started temporary existing Next.js webpack and FastAPI servers because ports 3000/8000 were unused; no tools or dependencies were installed
- Filled and submitted October 10–11, 2026, two travelers, budget 3000, accommodation near Jing'an Temple, balanced pace. POST /trips/plan returned 200 and displayed the Mock result
- Actual SDK GET /_AMapService/v3/config/district now returned 200. Visually confirmed Shanghai roads, place names and AMap copyright; this was a rendered basemap, not merely a map container. Panning changed the visible area, and double-click zoom changed geographic detail
- Chrome responsive viewport 400 × 748: map, search controls, attribution and timeout message fit the single-column layout without obvious horizontal clipping. This is a basic browser-emulation check, not a real-phone/touch certification
- Real POI searches in this follow-up: 武康大楼 twice and 静安寺 once returned 504. The page showed the safe timeout message and remained usable. A separate single no-key/non-POI connectivity check with granted network permission also encountered ConnectTimeout (~3 seconds). This supports a connection-path problem, but does not identify whether DNS, local networking, an environment proxy or the upstream service is responsible
- No POI code or timeout was changed to mask the timeout. Search/card/marker synchronization, old-marker cleanup and fit-all remain **blocked for this follow-up**; the previous browser run's successful POI searches and offline marker tests are not substitutes
- Browser Network still shows GET /_AMapService/v3/log/init returning 404 (surfaced as ERR_BLOCKED_BY_ORB). This unallowlisted SDK logging request did not prevent the basemap/interaction checks; it was not exposed just to eliminate a diagnostic error. Console also showed existing preload and SDK canvas-performance warnings, plus the POI 504 errors; no claim of a clean console is made
- Page screenshots only; no raw network logs or credential-containing screenshots were saved. Evidence: `/tmp/trippilot-4a-browser-m8HHlP/01-real-basemap.png` (initial Shanghai map), `/tmp/trippilot-4a-fix-8gV1Fm/04-map-dragged.png`, `/tmp/trippilot-4a-fix-8gV1Fm/06-map-doubleclick-zoom.png`, `/tmp/trippilot-4a-fix-8gV1Fm/07-mobile-map.png`. These are local temporary artifacts, not repository files
- Remaining acceptance: after POI connectivity recovers, verify real search → markers, card ↔ marker selection, a second query replacing markers and fit-all. Full 4A acceptance is not yet claimed. Both temporary test servers were stopped after verification; restart them for manual testing. No commit/push or 4B work

## Milestone 4A Initial Verification (2026-10-04, before the browser follow-up)

### Automated checks — passed, offline

| Command | Result |
| --- | --- |
| `cd frontend && npm run lint` | Passed |
| `cd frontend && npm run typecheck` | Passed (`next typegen && tsc --noEmit`) |
| `cd frontend && npm run test` | 4 files / 76 tests passed |
| `cd backend && .venv/bin/python -B -m unittest discover -s tests -v` | 46 tests passed: all original 27 plus 19 proxy tests |
| `git diff --check` | Passed |

- Frontend tests use Vitest/jsdom/Testing Library, mocked fetch and a mocked SDK; dotenv loading is disabled and test credentials are fictional. No real AMap calls are made by these tests
- Final exact-value checks found no configured credentials in the 28 changed/added deliverable files and no backend Web Key/security code in the browser static build. Only blank-value environment templates are part of the changes; generated Python cache changes were excluded
- Covered explicit-submit search, empty/error states, stale responses, card/marker selection, repeated selection after fit-all, longitude/latitude order, invalid coordinates, SDK configuration/failure, timeout, StrictMode and unmount cleanup
- Backend tests use httpx.MockTransport and isolated settings. Covered fixed upstream, illegal targets/paths/methods/parameters, incorrect/missing Key, client security-code override, JSONP validation, timeout, upstream errors, response size/type limits, secret reflection, log redaction, CORS and original trip/POI behavior

### Build — fallback passed without map configuration

- `cd frontend && NEXT_PUBLIC_AMAP_JS_KEY= NEXT_PUBLIC_AMAP_SERVICE_HOST= npm run build` — failed: sandbox Google Fonts download failure; network-enabled retry reached the existing Turbopack internal port-binding restriction
- `cd frontend && NEXT_PUBLIC_AMAP_JS_KEY= NEXT_PUBLIC_AMAP_SERVICE_HOST= npm run build -- --webpack` — passed, including TypeScript and static page generation; needed network permission for the pre-existing Google Fonts setup
- Empty process variables deliberately override local map values: missing map configuration does not break the production build. No build/font configuration was changed

### Real-service HTTP checks — passed for the following checks only

- Used the existing local FastAPI service on 127.0.0.1:8000; GET /health returned 200. The user's existing server was left running
- Confirmed configuration presence, frontend/backend JS Key equality and the expected local serviceHost without printing values. Local environment files are ignored and untracked
- Real GET /places/search for keyword=武康路 and city=上海 returned 200; all 20 results passed Place validation
- Real GET /_AMapService/v3/config/district using the configured JS Key, Shanghai-only options, s=rsv3 and a validated JSONP callback returned 200 with the expected callback wrapper; credentials were absent from the response
- An initial non-SDK-style district probe without s=rsv3 was rejected by AMap with code 10009; the proxy safely returned 502. The SDK-style request succeeded; no credential values were recorded
- A diagnostic custom-style probe returned an upstream invalid-parameters error. The official documentation identifies this service as optional for custom styles; this release uses the default map style, so the unused styles proxy was removed instead of broadening its permissions
- POST /trips/plan still returned a validated two-day Mock TripPlan (200); allowed-origin CORS was confirmed on live local responses
- These are real HTTP checks, not a browser execution of the SDK. They do not verify tile rendering, SDK-generated request compatibility, actual marker clicks or mobile layout

### Manual acceptance — pending

1. Restart the frontend with local public configuration (use `npm run dev -- --webpack` if the default development bundler is restricted), keep the backend running, and submit the existing trip form
2. Confirm the result page shows Shanghai's real map, visible AMap attribution, no location permission request, and clearly separated Mock itinerary/weather/cost labels
3. Search 武康路: confirm candidate names/addresses and markers. Click different cards and markers to verify both directions, highlighting, centering and list scrolling; check that no candidate is selected by default
4. Click “查看全部搜索结果”; confirm all valid markers are visible. Click the already-selected card again and confirm it recenters. Verify no route line is drawn and no itinerary activity is changed
5. Try different queries rapidly, an empty query, no results and a backend/network failure. Confirm clear status messages, no stale results and recovery on a new search
6. Switch itinerary days, return to the form and submit again; confirm no duplicate maps/markers or residual listeners. Inspect a 375–400 px responsive viewport for map/card overflow, usable controls and unobscured attribution
7. Inspect browser requests locally: POI search must go through FastAPI; SDK service calls must stay within the allowed serviceHost route. The browser may receive the public JS Key, but must never receive AMAP_WEB_KEY or AMAP_JS_SECURITY_CODE. Do not share unredacted network logs
8. If the real SDK needs an additional path/parameter, treat it as an acceptance blocker: record only a redacted method/path/parameter-name description, verify it against official documentation, and add the narrowest implementation/test. Do not enable a catch-all proxy

- Full real-map/browser acceptance is still pending. No Browser Agent or Playwright was used, so mobile layout and actual SDK behavior have not been visually certified
- No commit or push was performed. Work stops at 4A; 4B activity-to-POI binding remains unimplemented

### Changed files and dependencies

- Backend modified: `backend/.env.example`, `backend/app/config.py`, `backend/app/integrations/amap.py`, `backend/main.py`, `backend/tests/test_places.py`
- Backend added: `backend/app/api/amap_proxy.py`, `backend/app/integrations/amap_proxy.py`, `backend/app/logging_filters.py`, `backend/tests/test_amap_proxy.py`
- Frontend modified: `frontend/.gitignore`, `frontend/package.json`, `frontend/package-lock.json`, `frontend/src/components/trip-plan-result.tsx`
- Frontend added: `frontend/.env.example`, `frontend/vitest.config.mts`, `frontend/src/types/place.ts`, `frontend/src/types/amap.d.ts`, `frontend/src/lib/places-api.ts`, `frontend/src/lib/amap-loader.ts`, `frontend/src/components/places/place-explorer.tsx`, `frontend/src/components/places/place-search.tsx`, `frontend/src/components/places/place-map.tsx`
- Frontend tests added: `frontend/src/test/setup.ts`, `frontend/src/types/place.test.ts`, `frontend/src/lib/places-api.test.ts`, `frontend/src/lib/amap-loader.test.ts`, `frontend/src/components/places/place-explorer.test.tsx`
- Documentation: `docs/PROGRESS.md` (preserved the pre-existing 2026-10-02 verification notes)
- Runtime dependency: `@amap/amap-jsapi-loader`; development dependencies: `@amap/amap-jsapi-types`, `vitest` 4, `jsdom`, `@testing-library/react`, `@testing-library/dom`, `@vitejs/plugin-react` 5. Added `test` and `typecheck` scripts. No backend dependency was added
- Vitest 5 was not installed because its Node type peer requirement conflicts with the repository's existing `@types/node` 20; compatible Vitest 4 was installed without force flags
- Read-only `cd frontend && npm audit --json` reported 7 existing-framework/tooling findings (6 high, 1 critical), including Next 16.3.3 and the ESLint glob dependency chain; the affected baseline packages predate 4A. No automatic audit fix, downgrade or framework upgrade was performed

## Verification (2026-10-02)

- Confirmed that backend settings read a non-empty AMAP_WEB_KEY without displaying its value; backend/.env is ignored by Git and is not tracked
- Used the existing local TripPilot server on 127.0.0.1:8000; GET /health returned 200
- Actual GET /places/search with keyword=武康路 and city=上海 returned 200 after a real AMap request; all 20 results passed the Place model validation
- Results included 武康路 and 武康路历史文化名街 with addresses, coordinates, categories and source="amap"; the credential was absent from the response
- Offline backend regression tests: 27/27 passed, including existing trip tests; git diff --check passed. The existing Starlette/httpx deprecation warning remains
- No business code, frontend or trip-planning behavior was changed; the pre-existing local server was left running. No commit or push was performed during this verification

## Verification (2026-09-15)

- Backend: `cd backend && .venv/bin/python -B -m unittest discover -s tests -v` — 27 tests passed (16 new and all 11 existing tests)
- Tests use httpx.MockTransport and isolated settings; no real AMap API or local credentials are used
- Covered normal conversion and v5 parameter/timeout mapping, empty results, invalid/missing locations, invalid identity, nullable optional fields, upstream status/HTTP/network errors, redirects, timeouts, malformed JSON/response shape, key absence, query validation, GET CORS, key redaction, client closure and dotenv/environment precedence
- Python syntax checks and `git diff --check` passed; no standalone backend lint command is currently configured
- FastAPI started successfully on 127.0.0.1:8000 with an explicitly empty AMAP_WEB_KEY; actual HTTP GET /health returned 200 and /places/search returned the expected safe 503 response
- Local server checks needed execution-environment network/port permission; the temporary server was stopped after verification
- Real AMap responses were not requested or verified in this run
- Frontend and existing trip models/API were not changed; frontend lint/build were not rerun for this backend-only task

## Verification (2026-09-10)

- Backend: `cd backend && .venv/bin/python -m unittest discover -s tests -v` — 11 tests passed, including budget reconciliation, activity/transport links, date rollover, weather validation, request validation, CORS and health
- Frontend: `cd frontend && npm run lint` — passed
- `npm run build` — attempted; blocked first by Google Fonts network access and then by the environment's Turbopack internal port-binding restriction
- `npm run build -- --webpack` — passed, including TypeScript and static page generation; no build configuration was changed
- FastAPI started successfully at `http://127.0.0.1:8000`; production frontend started at `http://127.0.0.1:3000`
- Chrome UI: submitted the actual form, received the backend result and displayed overview, timeline, transport, budget, weather and map placeholder
- Chrome UI: Day 2 selection updated activities, weather date/condition/rain risk and ordered places; switching back to Day 1 worked
- Chrome UI: “修改旅行需求” returned to the form with dates, budget, travelers, accommodation, pace and interests preserved; resubmission succeeded
- Chrome responsive mode (400 × 748): visually checked overview, activity cards, budget/weather/map sections and the form; single-column layout and text wrapping showed no obvious clipping or overlap, and mobile form submission reached the full result
- Native browser interaction was used; no Playwright or new browser-testing dependency was introduced
- `git diff --check` — passed

## Verification (2026-09-09)

- Backend: `cd backend && .venv/bin/python -m unittest discover -s tests -v` — 7 tests passed, including subcases for required fields, invalid values, supported durations/paces, response shape, CORS and health
- Frontend: `cd frontend && npm run lint` — passed
- Default `npm run build` — attempted; blocked by Google Fonts network access, then the environment's Turbopack internal port-binding restriction
- `npm run build -- --webpack` — passed, including TypeScript and static page generation
- FastAPI launched successfully on `http://127.0.0.1:8000`
- Live HTTP: GET /health returned 200; POST /trips/plan returned 200 with two days / six activities; a negative budget returned 422; allowed-origin CORS response confirmed
- Production frontend served successfully on `http://127.0.0.1:3000`
- Safari UI: submitted the actual form and displayed the backend's Shanghai two-day plan, Mock cost of CNY 1,000.00, six activities and their times
- Safari UI: whitespace-only accommodation triggered the backend 422 error message; correcting the value and resubmitting displayed the plan again

## Local Run

- Backend: `cd backend && .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000`
- Frontend: `cd frontend && npm run dev` (port 3000)
- If Turbopack is unavailable in the execution environment, use `npm run dev -- --webpack`, or build with `npm run build -- --webpack` and run `npm run start -- --hostname 127.0.0.1 --port 3000`
- Formal entry is `/`: confirming requirements is local, and explicit generation targets `http://127.0.0.1:8000/trips/schedule-preview`. `/trips/plan` is retained only for the independent `/demo` legacy integration flow; both services must run for data queries. Historical copies use `/plan/from/[id]`. See `docs/DEMO_6C1.md`.
- POI search: create local `backend/.env` using `backend/.env.example` as a template and set `AMAP_WEB_KEY`; never commit the real file or key. Process environment takes priority, including an explicitly empty key. The dotenv path is resolved relative to the backend code, not the working directory
- POI smoke check: `curl --get --data-urlencode 'keyword=武康路' --data-urlencode 'city=上海' http://127.0.0.1:8000/places/search`
- Map configuration: use `frontend/.env.example` and `backend/.env.example` as templates. Set frontend `NEXT_PUBLIC_AMAP_JS_KEY` to the browser JS Key and `NEXT_PUBLIC_AMAP_SERVICE_HOST` to `http://127.0.0.1:8000/_AMapService`. Set backend `AMAP_JS_KEY` to the same JS Key and keep `AMAP_JS_SECURITY_CODE` only on the backend; preserve `AMAP_WEB_KEY` for POI search
- Public map variables are compiled into the frontend. After changing them, restart development mode or rebuild production mode. Earlier 4A offline verification used empty map configuration; the latest 4B Chrome acceptance instead used a configured webpack production build
- Without keys, FastAPI still starts and the health/Mock trip APIs work. Missing Web Key gives a safe POI 503; missing backend JS configuration gives a safe proxy 503; missing public configuration displays a map setup message while leaving the trip UI intact
- Official proxy reference: https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode . The standard/default map does not enable the optional custom-style proxy

## Known Issues

- Default Turbopack build is blocked by a port-binding permission restriction in the agent environment; webpack build succeeds
- Existing next/font/google setup requires Google Fonts access during a fresh build
- Existing Starlette TestClient emits an httpx deprecation warning; tests pass without changing dependencies
- PROJECT_CONTEXT.md specifies Python 3.12, while backend/pyproject.toml requires Python >=3.13; verification used the existing Python 3.13 environment
- Real basemap, panning, double-click zoom and basic 400px layout passed after the proxy fix; the latest focused real search/marker/replacement/fit-all acceptance also passed after the bounded connection-retry follow-up. External networking can still fail despite two successful live queries; physical mobile/touch and broader real-browser edge cases remain pending
- The SDK's unallowlisted /v3/log/init request remains 404/ORB; observed map rendering and interactions work without it. Deployment on a different origin/port requires updating the current local appname allowlist along with CORS
- `npm audit` reports 7 existing dependency findings (6 high, 1 critical), including a Next.js advisory affecting the current 16.3.3 baseline. Review before deployment; no framework dependency remediation was included in 4A
