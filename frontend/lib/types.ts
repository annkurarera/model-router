export type Mode = "cheapest" | "balanced" | "quality";

export interface Attempt {
  model: string;
  outcome: "ok" | "error" | "skipped_breaker" | "skipped_budget";
  error_category: string | null;
  latency_ms: number;
  retry: boolean;
}

export interface RouterMeta {
  routed_to: string;
  provider_model: string;
  reasoning_effort: string | null;
  category: string | null;
  difficulty: string | null;
  confidence: number | null;
  reason: string;
  policy_version: string;
  mode: Mode;
  forced: boolean;
  compare: boolean;
  signals: string[];
  downgraded: boolean;
  fallbacks_used: number;
  attempts: Attempt[];
  schema_valid: boolean | null;
  estimated_cost_usd: number;
  cost_usd: number;
  router_overhead_usd: number;
  cost_basis: "simulated" | "calculated";
  price_checked: string;
  reference_model: string;
  reference_cost_usd: number | null;
  latency_ms: number;
}

export interface ChatResponse {
  id: string;
  model: string;
  choices: { index: number; message: { role: string; content: string }; finish_reason: string }[];
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  router: RouterMeta;
}

export interface RouterErrorBody {
  message: string;
  code: string;
  request_id?: string;
  attempts?: Attempt[];
  cheapest_qualifying_model?: string;
  cheapest_estimated_cost_usd?: number;
}

export interface Health {
  status: string;
  demo_mode: boolean;
  policy_version: string;
  models_enabled: string[];
  price_checked: string;
}

export interface ModelOption {
  id: string;
  provider_model: string;
  tier: "tiny" | "mid" | "strong" | "reasoning";
  reasoning_effort: string | null;
  input_usd_per_mtok: number;
  output_usd_per_mtok: number;
  per_call_usd?: number;
  web_search?: boolean;
  demo_allowed: boolean;
  reference?: boolean;
}

export interface Stats {
  requests: number;
  answered_pct: number | null;
  total_cost_usd: number;
  avg_cost_per_answer_usd: number | null;
  compare_runs: number;
  compare_spend_usd: number;
  reference_cost_usd: number;
  spent_today_usd: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  fallback_rate_pct: number | null;
  requests_by_model: Record<string, number>;
  requests_by_category: Record<string, number>;
  errors_by_status: Record<string, number>;
  cost_basis: "simulated" | "calculated";
  price_checked: string;
}

/** One routed request kept in this browser session's history. */
export interface HistoryEntry {
  key: string;
  prompt: string;
  at: Date;
  mode: Mode;
  response?: ChatResponse;
  error?: RouterErrorBody;
  status?: number;
}
