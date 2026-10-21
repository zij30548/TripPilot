# TripPilot Agent Instructions

Read in order before product or architecture work:

1. README.md
2. docs/PROJECT_CONTEXT.md
3. docs/ROADMAP.md
4. docs/PROGRESS.md

Then use docs/README.md to select relevant detail. Read applicable directory instructions; before frontend code changes follow frontend/AGENTS.md and installed Next.js docs. Archive documents are historical evidence, never current instructions or new authorization.

## Scope and current facts

- Verify actual HEAD, branch, worktree, code and running services. Preserve unrelated changes; do not reset or infer state from old chats.
- Main product has exactly three navigation surfaces: `/` plain dialogue, `/knowledge` local reference retrieval, `/evaluation` owner-scoped execution/technical checks. No travel cards/planner/trace buttons in the main dialogue; media/map/requirements-edit modals open only explicitly. `dialogue-v2` maps 38 finite actions to existing headless domain controllers and fixed guidance; inspect/update/clarify `dialogue-v1` and legacy callers remain compatible. Read docs/TRAVEL_DIALOGUE_INTEGRATION.md and the historical docs/THREE_SURFACE_UI.md. Models propose actions, never free-form business facts; bounded selection labels are not fact sources. Domain mutations/external calls retain explicit confirmation and current-context checks. Server proposal receipts do not prove browser execution; local program messages/planning are not durable server recovery. Echo runtime retains native feedback, role routing, Skills, 3-model/4-tool/45s budgets, durable receipts and explicit preferences. Controlled checks/old scores are not new live semantic acceptance; this integration made zero real model/AMap calls and needs new authorization for live quality verification.
- Backend runtime sources are now api/, agents/, core/ (including schemas), tooling/travel, tooling/providers, memory/, monitor/, evaluation/ and skills/. The uploaded EchoMind/ reference is excluded from delivery; do not copy its env, dependencies or data. Original app/ cache paths remain protected but contain no current Python source. Do not restore a duplicate app implementation to hide migration failures.
- Chat city IDs/catalogue are separate from legacy DestinationId. Never globally loosen legacy six-city routing/weather/map/archive contracts. Preserve multi-city user intent and clarify; reject wrong-city POI data, but do not silently discard a requested city or claim cross-city execution. Catalogue source restrictions/update strategy require production review.
- Current travel tools: Shanghai/Hangzhou/Beijing/Guangzhou/Chengdu/Xi’an, one city, 1–7 days, one accommodation, 6 required/3 optional. These are current boundaries, not permanent goals. Empty days are valid; no automatic balancing or optimality. Chengdu's complete transit sample remains unaccepted (`data_error`).
- New formal saves use v3; v1/v2 retain strict original 1–3-day semantics. Snapshot validation is not upstream authenticity certification. Preserve POI identity, real data, unknowns, failures and source/query times; never invent travel facts or make old data look newly verified.
- Photo/selected-video-frame recognition only offers one clue or refusal, then explicit AMap search/preview/user confirmation. Alias resolution or same-name POI is not photo identification. No automatic city/plan change, batch video recognition or media archiving. Limits, historical failures and exhausted ledgers remain in MEDIA_1/MEDIA_2A/MEDIA_2B; limited samples are not general reliability.
- Knowledge generative Q&A remains disabled. MySQL production cutover and new-chat production backup/recovery remain gated; user-reported HTTPS/login is not independent verification of the new version. Use current PROGRESS for the actual stopping point.

## Authorization and Harness

- Work on one explicitly authorized closure. A roadmap is not implementation, deployment or paid-call permission; do not automatically begin the next stage.
- Read docs/HARNESS_ENGINEERING.md for Agent/runtime, architecture, stage-planning or evaluation work. Its H01–H12 checklist covers development, runtime and evaluation separately. Report verified, unverified, planned or justified not-applicable states; documentation coverage is not implemented capability or industry certification.
- Enforce invariants with strict contracts and relevant executable checks, not prompts alone. Preserve authority/version checks, finite execution, durable attempts, explicit confirmation and no blind retry of unknown outcomes. Recurring failures need scoped regression checks.
- Real model calls require new explicit applicable authorization and remaining budget. Count failures/sent-cancellations as required; never reset or borrow allowances through restart, stage/batch changes or new conversations. Product quotas are not agent acceptance authorization.
- Echo migration's separate 2026-10-22 live authorization used 5/8 model sends and 4 physical AMap sends; all planned scopes are consumed/closed, unused3 are not a follow-up allowance. Initial api01 failed and is not replayed/regraded; explicit strict-index compatibility continuation passed the remaining3 known scenarios, not a same-version4/4 or general-quality claim. Oldchat9/12 remains paused. Read ECHOMIND_MIGRATION for original/continuation hashes and current UI/readback evidence.
- Do not add dependencies/frameworks/infrastructure (including Docker, Redis, PostgreSQL, LangGraph, MCP or extra Agents) merely to match a checklist; require task scope and actual value. Keep TypeScript frontend and typed Python backend.

## Identity, storage and external boundaries

- Read docs/MYSQL_REGISTRATION.md before identity/storage/usage/deployment changes. Explicit TRIPPILOT_MYSQL_URL selects accounts, sessions, invitations, archives, durable quotas AND chat extension in the configured MySQL store; no SQLite fallback, runtime migration/DDL or quota reset. Old stores lacking chat extension fail chat explicitly.
- Schema changes/copy/restore need isolated empty targets, private new receipts and original identity anchor; preserve IDs, ownership, times, v1-v3 snapshots and every quota-attempt state. chat-places-storage-v1 and assistant-storage-v1 are explicit, not runtime DDL. Validate source contents before target DDL and stop writers; two matching snapshots do not prove no future write. DDL is not rollback-safe; uncertain COMMIT is not permission to retry. Never auto-switch production or restore old allowances/sessions. Old copy/backup tools must refuse to omit assistant/chat/places; use the reviewed explicit extension copier.
- Legacy SQLite/user files remain protected; never write to old backend/data/saved-trips.sqlite3 or auto-assign ownerless records. Local testing uses independent data. Existing normal-account automatic login is not a runtime auth bypass; never add a production skip-login/master password.
- Backend business/media/external endpoints require session; unsafe requests require trusted Origin/CSRF. Public scope is GET /health, GET /auth/session, POST /auth/login, POST /auth/register and non-executing CORS preflight. Client owner IDs or hidden buttons are not authorization.
- Logout/expiry/account change clears prior UI and rejects late responses. Preserve AbortController, monotonic versions/epochs and operation-time checks, including A→B→A. Preview is not adoption; adoption/save require their existing explicit, consistent/idempotent paths.
- Secrets, JS security codes and trusted upstream configuration stay server-side; only the browser-specific public map key may be public. Preserve fixed upstream/path/parameter limits. Logs must not contain credentials, cookies, private prompts, media bodies or model chain of thought.
- Persistent account/global attempt quotas and request/resource budgets apply before actual sending/processing. Fail closed on counting failure; failures/cancellation/unknown dispatch do not imply refunds. Attempt limits are not exact RMB caps; browser SDK/tiles and ingress need separate controls.

## Verification, handoff and protected work

- Run checks proportional to actual changes and report only what ran. Distinguish controlled tests, replay, real model/upstream, Chrome and Linux/server evidence. Pure docs require links, commands/facts, secrets and diff checks—not invented full test reruns. Do not execute paid acceptance tools as ordinary tests.
- Every stage explains the user problem, actual data flow/code, tradeoffs, evidence, a real failure/limit and stopping point; include a short oral explanation and 3–5 evidence-backed interview follow-ups (docs/AGENT_EVOLUTION.md). Do not invent scale, benchmark alignment or contributions.
- Update current PROGRESS and relevant detail at closure. Keep current facts separate from plans; preserve historical scores, failures, ledgers and acceptance meaning in archive/topic reports. Do not silently rewrite history or declare blocked/unverified work complete.
- Protect original databases/sidecars/anchors, private config, old evidence, unrelated edits and the five recorded Python caches. Use Python -B; do not reset/clean/stage them. Keep frontend/CLAUDE.md and directory instructions unless explicitly changing their integration.
- Never commit secrets, .env, databases, private data, media, screenshots or browser logs. Stage only reviewed delivery files; normal commit/push when authorized, never force-push. Code synchronization is not cloud deployment.
