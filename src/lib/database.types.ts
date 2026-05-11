/**
 * Types matching the data shape from the scoring pipeline.
 *
 * The pipeline outputs snake_case JSON (matching the Supabase columns).
 * The hook layer maps those to camelCase for the React frontend.
 */

// ── Scoring pipeline sub-types ─────────────────────────────────

export interface GatingResult {
  status: 'passed' | 'failed' | 'needs_review';
  eligibility: { pass: boolean; confidence: number; reasoning: string };
  geography: { pass: boolean; reasoning: string };
}

export interface ScoringBreakdown {
  strategic_fit: { score: number; reasoning: string };
  funding_value: { amount_used: number };
  probability: { score: number; reasoning: string };
  effort: { score: number; reasoning: string };
  strategic_value: { score: number; reasoning: string };
}
