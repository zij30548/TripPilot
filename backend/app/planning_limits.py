"""Shared current limits; old archive semantics explicitly use legacy limits."""
import json
from pathlib import Path

LIMITS = json.loads((Path(__file__).resolve().parents[2] / "shared/planning-limits.json").read_text())
MAX_TRIP_DAYS: int = LIMITS["max_trip_days"]
LEGACY_MAX_TRIP_DAYS: int = LIMITS["legacy_max_trip_days"]
MAX_LOGICAL_EDGES: int = LIMITS["max_logical_edges"]
MAX_STARTED_ROUTES: int = LIMITS["max_started_routes"]
