# TripPilot Agent Instructions

Always read:

- docs/PROJECT_CONTEXT.md
- docs/PROGRESS.md

before making architectural or product decisions.

## Rules

- Work on one focused task at a time.
- Do not expand product scope without explicit instruction.
- Do not invent travel data.
- Do not expose API keys to the frontend.
- Do not add Docker, Redis, PostgreSQL, LangGraph, MCP, or authentication unless explicitly required.
- Use TypeScript for frontend code.
- Use Python type annotations for backend code.
- Run relevant lint/tests after changes.
- Never commit secrets or .env files.