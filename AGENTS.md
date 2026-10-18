# TripPilot Agent Instructions

Always read:

- README.md
- docs/PROJECT_CONTEXT.md
- docs/ROADMAP.md
- docs/PROGRESS.md

in that order before making architectural or product decisions. Also read applicable directory instructions. Before changing frontend code, follow frontend/AGENTS.md and read the relevant installed Next.js documentation.

## Product facts, roadmap and authorization

- Verify actual HEAD, branch, worktree and relevant code; do not infer the repository state solely from conversation history or early planning documents.
- Keep current implemented/accepted capabilities separate from long-term goals. Shanghai, Hangzhou, Beijing, Guangzhou, Chengdu or Xi’an (one city per trip), 1–7 days and one accommodation reference are current version boundaries, not permanent product restrictions. Required/optional capacities remain 6/3; empty days are valid, and no automatic balancing is promised. Nationwide, further capacity and media-assisted travel are not implemented merely because they appear in the roadmap. Consult DURATION_1/DESTINATION_2/PROGRESS for live acceptance limits (Chengdu transit returned data_error, not a completed transit itinerary). New formal saves use v3; v1/v2 retain strict original 1–3-day semantics. Historical acceptance records retain their then-current scope.
- A roadmap is planning, not blanket implementation authorization. Work only on the explicitly authorized task; do not automatically begin the next item or stage.
- In PROGRESS, start with the latest summary and stopping point. Older acceptance records, failures, test counts and then-current plans remain historical evidence; do not overwrite or reinterpret them as this round's results.
- At each round's end update PROGRESS with actual changes, checks, evidence, unresolved items and a clear stopping point. Do not mark blocked or unverified work complete.
- Historical model allowances and ledgers are not new authorization. New real model calls require explicit applicable authorization and a remaining budget; count failed/sent-cancelled attempts as required. Do not reset quotas by restarting, opening a new conversation, changing stages or creating another ledger.
- Knowledge-base generative Q&A remains deferred and disabled. Existing itinerary AI tools do not authorize reopening it; multi-Agent architecture is not a prerequisite for the product roadmap.
- media-1 is closed out ONLY as single-photo clue assistance: one possible name/city or refusal → explicit AMap search → user verification and confirmation → required-place entry. Recognition may be wrong; neither city aliases nor a same-name POI proves photo identity. Current v2 has no free-form basis. The latest fixed set passed (clear 2/2, area-partial 2/2, reasonable empty refusal 4/4, structure 8/8): four recognitions, four refusals, eight inputs/seven originals with correlated crops and only Beijing/Guangzhou positives, one measurement each. This is not six-city/national reliability or public-launch acceptance. The documentation-only closeout reuses version-checked historical evidence; it is NOT a single final-version live end-to-end rerun. English gating was repaired via historical-output replay + real AMap, separate from live model quality. Original 12/12, conservative 12/12 and fixed 8/8 ledgers/scores remain independent and exhausted; old api02 still fails its old standard, old api06's exact structure error remains unknown. Consult MEDIA_1's limited acceptance matrix/demo, MEDIA_1_FIXED_VALIDATION and PROGRESS. The media-1 closeout remains valid; media-2A added frame preparation and the separately authorized media-2B adds explicitly selected single-frame clue recognition. No new real model calls without explicit authorization. Photos are not persisted; batch video/multiple-landmark recognition, optional import and automatic city/plan change are not implemented. This does not override roadmap authorization rules.

- Current stop: media-2B selected-single-frame clue assistance, NOT whole-video or batch landmark recognition. MP4/H.264 and media-2A resource limits remain; selection/extraction/enlargement never trigger model or AMap calls. Explicit recognition sends only one independently owned JPEG through the unchanged photo-landmark-v2 prompt/model; city checks, explicit POI search/preview/confirmation, six-required limit and transactional editing remain mandatory. Frame/video/city/close/edit-cancel changes synchronously invalidate recognition/search. This round's 8/8 authorized model calls are exhausted (4 API + 4 Chrome); five recognitions/three reasonable refusals, structure 8/8 and four limited flows passed, with correlated samples and two static-photo synthetic clips. Not six-city reliability or public-launch acceptance; no new calls without authorization. Consult MEDIA_2B for evidence/untested scope and MEDIA_2A for temporary files/cancellation/resource limits. Media is never archived; no automatic planning, batch/multiple-place discovery or next-stage implementation.

## Rules

- release-1D adds invitation-only authentication and per-account saved-trip isolation for controlled testing, not public-launch clearance. Read docs/RELEASE_1D.md before identity/storage/deployment work. Every backend business/external/media endpoint requires a server session; unsafe calls also require CSRF and trusted Origin. Only GET /health, GET /auth/session, POST /auth/login and non-executing CORS preflight are public. Knowledge Q&A stays disabled after authentication. Never add a runtime auth bypass for tests.
- New private storage defaults to backend/data/trippilot-private.sqlite3 (trusted absolute-path override). Never point it at the old backend/data/saved-trips.sqlite3. Migrate only a consistent copy; legacy ownerless rows remain quarantined, never assigned to the first account. Snapshot v1/v2/v3 semantics remain unchanged. Auth/ownership schema version is separate from snapshot version. Rehearse migration/rollback with temporary databases; no in-place restore or user-data reassignment without explicit authorization.
- Logout/expiry/account change clears the old identity's entire UI session, aborts pending work and rejects late responses, including body reads. Session Cookie/password/CSRF values are not evidence or log content. Login throttling is not an upstream business quota. Current release stop is release-1D; earlier media stop statements describe their own product branch, not permission to start new work.

- Work on one focused task at a time.
- Do not expand product scope without explicit instruction.
- Do not invent travel data.
- Keep server-side API keys and JS security codes out of the frontend. The existing browser-specific public map key does not make backend credentials public.
- Do not add Docker, Redis, PostgreSQL, LangGraph, MCP, or authentication unless explicitly required.
- Use TypeScript for frontend code.
- Use Python type annotations for backend code.
- Run relevant lint/tests after code changes and report what actually ran. For documentation-only work, check links, commands against the repository, sensitive information and git diff; do not invent a full test rerun.
- Preserve unrelated work, existing user data and the five recorded Python cache files; do not reset, clean or stage them as part of unrelated tasks. Use Python -B for checks that could otherwise alter bytecode caches.
- Never commit secrets, .env files, databases/sidecars, private data, acceptance screenshots or browser logs. Stage only explicitly reviewed delivery files, never all worktree changes indiscriminately.
