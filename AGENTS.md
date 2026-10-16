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
- media-1 adds a single-photo clue → explicit AMap search → confirmed required-place entry. Current v2 returns exactly one name/city for possible and no clues for uncertain/unrecognized; free-form basis was removed, not proven accurate. The earlier conservative run did NOT pass: its crop missed the then-frozen refusal standard and an English city label blocked Chrome search. English gating was separately repaired via exact six-city aliases, historical-output replay and real AMap; it does not relax POI ownership or regrade that run. Latest separately authorized fixed review used 8/8 calls with unchanged prompt/schema and MEDIA_1_EVALUATION_STANDARD: clear 2/2, area-partial 2/2, reasonable empty refusal 4/4, structure 8/8. Only this fixed set passed; not full media-1, six-city or national recognition acceptance. Eight inputs/seven originals include correlated crops and only Beijing/Guangzhou positives; refusals are not successful identifications. Consult MEDIA_1_FIXED_VALIDATION/MEDIA_1/PROGRESS. Original 12/12, conservative 12/12 and latest 8/8 are all exhausted and scores stay separate; old api02 remains failed under its old standard. Stop at the fixed-set report; no additional calls without new explicit authorization. Photos are not persisted; no video, optional import or automatic city/plan change is implemented. This does not override roadmap authorization rules.

## Rules

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
