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

## Current

- Milestone 4B is the committed baseline `0eeba167766216ffbaecfa4610c83539ae36577d` (`feat: complete Milestone 4B activity-to-POI binding`), committed/pushed before this authorized 4C-1 task. Its acceptance record remains below
- Milestone 4C-1 implemented and focused real Chrome acceptance passed in the actual project (2026-10-05). Frontend 164 tests, backend 78 tests, lint, typecheck and webpack production build passed. Default Turbopack's historical environment restriction remains documented, not retried this round; physical-phone/touch acceptance remains separate
- GET /places/search accepts keyword + city and maps them to AMap v5 keywords + region with city_limit=true, page 1 and page size 20
- Place returns id, name, nullable address/category, validated latitude/longitude, and source="amap"; invalid POIs are skipped without fabricating coordinates
- Missing key returns 503, upstream timeout 504, other upstream failures or malformed payloads 502, invalid query parameters 422, and empty valid results 200 with []
- The result page supports real Place search/preview and user-confirmed activity bindings. POST /trips/plan and its Mock activities, transport, weather and costs remain unchanged; bindings are separate result-local React state, not a backend TripPlan mutation
- Valid 1-3 day requests use a fixed two-day activity template; dates are aligned to the requested start date and the validated request is echoed for the overview
- A notice explains when the requested duration differs from the two-day example; the fixture is not optimized for people, budget, preferences or daily time constraints
- Activity and transport costs reconcile to a fictional CNY 460 total for all travelers (transport 40, food 240, tickets 100, other 80); remaining or exceeded budget is derived from the submitted budget
- Weather, original transport, costs and activity times remain clearly marked Mock. Map numbers identify search candidates or deduplicated daily bound POIs. A separate blue walking polyline appears only after a successful explicit real query, and only in the daily bound view. Real walking estimates do not validate or overwrite Mock information. Replanning remains disabled
- Search is explicitly submitted, fixed to Shanghai, and uses GET /places/search. Candidate/marker selection only previews; activity binding requires a separate “确认绑定”. Each new binding session clears candidates/selection, and abort/request ID/context checks prevent stale requests from affecting a different activity/day/result
- Bindings are keyed by day number + date + activity ID, retained across day switches, and removed on editing/regenerating or refreshing. Multiple activities may share one POI marker; unbinding one activity preserves the others. No localStorage, database or server persistence is used
- SDK loading is shared and browser-only. Shanghai's center is obtained via DistrictSearch before map construction; no hardcoded POI coordinates, IP/GPS initialization or geolocation plugin is used
- Map creation is independent of candidate updates; search cancellation/request IDs prevent stale results, and unmount cleans listeners, markers, map and late async results
- Only GET /_AMapService/v3/config/district is proxied to https://restapi.amap.com/v3/config/district, limited to Shanghai/province/subdistrict=0/extensions=base/page=1. No catch-all proxy, custom-style endpoint, POI bypass, routing, weather or IP endpoint is allowed
- serviceHost is set before SDK loading. Every supplied JS Key must match backend configuration; jscode is appended only by the backend. Identical, individually validated key/s copies from the real SDK are folded; other duplicate/unknown parameters are rejected. Known SDK diagnostic fields are validated and stripped. Client security-code overrides, unsafe paths/callbacks, redirects and sensitive upstream responses are still rejected; TLS verification and the existing explicit CORS origins are retained
- 4C-1 adds only an independent walking endpoint and result-local route reference. No transit/driving, scheduling, real weather, LLM/Agent, database, global state manager, Browser Agent or Playwright was added; backend SDK proxy restrictions and credentials remain unchanged
- POST /routes/walking accepts origin/destination POI IDs and finite longitude/latitude, uses the fixed AMap walking upstream and returns source, UTC query time, status and normalized meters/seconds/coordinate segments. Only the first upstream proposal is validated and used; an invalid first proposal fails safely. No straight-line or Mock fallback exists
- Walking queries follow original activity adjacency, not a filtered list of bound activities. Missing endpoints and same-place pairs do not trigger a route request. Replacement/removal of an endpoint, day switch, edit/regeneration and unmount invalidate route state and pending requests; day bindings themselves remain preserved

## Next

- 4C-1 review accepted; normal commit/push of the 18 listed deliverables is authorized for closeout. Stop afterward; 4C-2 is not authorized. Physical-phone/touch acceptance remains available for manual review
- Review existing dependency security advisories as a separate, approved maintenance task before deployment

## Milestone 4C-1 Actual Implementation and Acceptance (2026-10-05, latest)

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
- Browser requests target `http://127.0.0.1:8000/trips/plan`; both services must be running locally
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
