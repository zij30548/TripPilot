import type { Candidate, CandidateResponse } from "@/types/candidates";
import type { Place } from "@/types/place";

// Internal provenance, deliberately NOT a CandidateResponse or a search batch.
export type HistoricalCandidates = { kind: "history"; recordId: string; name: string; savedAt: string; places: Place[] };
export type CandidatePoolSnapshot = {
  response: CandidateResponse | null;
  history?: HistoricalCandidates | null;
  lastAttempt: CandidateResponse | null;
  excludedIds: ReadonlySet<string>;
  loading: boolean;
  showingPrevious: boolean;
  message: string | null;
};
export const emptyCandidateSnapshot = (): CandidatePoolSnapshot => ({ response: null, lastAttempt: null, excludedIds: new Set(), loading: false, showingPrevious: false, message: null });
export function candidateItems(input: Pick<CandidatePoolSnapshot, "response" | "history">, required: Place[] = []): Candidate[] {
  return input.response?.candidates ?? [
    ...required.map(place => ({ place, role: "must_visit" as const, retrieval_sources: [] })),
    ...(input.history?.places ?? []).map(place => ({ place, role: "optional" as const, retrieval_sources: [] })),
  ];
}
