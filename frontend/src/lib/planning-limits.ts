// One data source shared with Python. Archive callers opt into legacy limits.
import limits from "../../../shared/planning-limits.json";
export const PLANNING_LIMITS = Object.freeze(limits);
export const MAX_TRIP_DAYS = limits.max_trip_days;
export const LEGACY_MAX_TRIP_DAYS = limits.legacy_max_trip_days;
