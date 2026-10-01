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

## Current

- Milestone 3: independent AMap POI search implemented and mock-tested (2026-09-15); live AMap verification remains pending
- GET /places/search accepts keyword + city and maps them to AMap v5 keywords + region with city_limit=true, page 1 and page size 20
- Place returns id, name, nullable address/category, validated latitude/longitude, and source="amap"; invalid POIs are skipped without fabricating coordinates
- Missing key returns 503, upstream timeout 504, other upstream failures or malformed payloads 502, invalid query parameters 422, and empty valid results 200 with []
- Milestone 2's Trip Result UI and POST /trips/plan remain unchanged and use Mock data, independent of Place search
- Valid 1-3 day requests use a fixed two-day activity template; dates are aligned to the requested start date and the validated request is echoed for the overview
- A notice explains when the requested duration differs from the two-day example; the fixture is not optimized for people, budget, preferences or daily time constraints
- Activity and transport costs reconcile to a fictional CNY 460 total for all travelers (transport 40, food 240, tickets 100, other 80); remaining or exceeded budget is derived from the submitted budget
- Weather, transport, costs and activities are clearly marked Mock; the map only shows place order, and replanning is a disabled placeholder
- AMap POI search is the only real-service integration added; no map, routing, weather, LLM, database, global state library or new dependency was added

## Next

- Await the user's next scoped task; no next-stage work started
- To verify live POI data, configure a valid AMap Web Service key locally and manually test /places/search; automatic tests must remain offline

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
- Without a key, FastAPI still starts and the health/Mock trip APIs work; only Place search returns 503. No frontend search UI or Trip Planner integration is included in Milestone 3

## Known Issues

- Default Turbopack build is blocked by a port-binding permission restriction in the agent environment; webpack build succeeds
- Existing next/font/google setup requires Google Fonts access during a fresh build
- Existing Starlette TestClient emits an httpx deprecation warning; tests pass without changing dependencies
- PROJECT_CONTEXT.md specifies Python 3.12, while backend/pyproject.toml requires Python >=3.13; verification used the existing Python 3.13 environment
