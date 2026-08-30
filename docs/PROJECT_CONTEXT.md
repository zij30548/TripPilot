# TripPilot Project Context

## Product

TripPilot is an executable and dynamically replannable AI travel agent.

## Current MVP

The first version supports:

- Shanghai
- 1-3 day trips
- budget constraints
- travel preferences
- must-visit places
- daily itinerary generation
- real route data
- weather-aware replanning

## Current Scope

The MVP does NOT initially support:

- user authentication
- automatic payment
- train ticket purchasing
- flight purchasing
- nationwide China
- Japan
- MCP
- multi-agent architecture

## Architecture

Frontend:
- Next.js
- React
- TypeScript
- Tailwind CSS

Backend:
- Python 3.12
- FastAPI
- Pydantic
- SQLModel

Planned integrations:
- AMap
- Weather API
- LLM
- OR-Tools
- Playwright

## Core Principle

LLMs may understand user intent and explain recommendations.

LLMs must not invent:

- transport duration
- prices
- weather
- opening hours

Deterministic code and external data must validate these values.