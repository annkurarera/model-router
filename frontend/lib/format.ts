/** Dollar amounts here are fractions of a cent, so show enough digits to compare them. */
export function usd(v: number | null | undefined): string {
  if (v === null || v === undefined) return "n/a";
  if (v === 0) return "$0";
  if (v >= 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(6)}`;
}

/** Same amount per 1,000 requests: easier to read than millionths of a dollar. */
export function per1000(v: number): string {
  const total = v * 1000;
  return total >= 1 ? `$${total.toFixed(2)}` : `$${total.toFixed(3)}`;
}

export function ms(v: number | null | undefined): string {
  if (v === null || v === undefined) return "n/a";
  return v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${v} ms`;
}

export function savedPct(cost: number, reference: number | null): number | null {
  if (!reference || reference <= 0) return null;
  return Math.round((1 - cost / reference) * 100);
}

const ERROR_WORDS: Record<string, string> = {
  timeout: "timed out",
  rate_limit: "rate limited",
  server_error: "server error",
  connection: "connection failed",
  auth: "key rejected",
  bad_request: "request rejected",
  context_length: "prompt too long",
  not_found: "model unavailable",
  invalid_response: "empty answer",
  unknown: "failed",
};

export function errorWords(category: string | null): string {
  return category ? ERROR_WORDS[category] ?? category.replace(/_/g, " ") : "failed";
}

export function clock(d: Date): string {
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

const CATEGORY_WORDS: Record<string, string> = {
  complaint: "customer complaint",
  loan_maths: "loan maths",
  extraction: "document extraction",
  sql: "SQL",
  python: "Python",
  live_data: "live web question",
  general: "general question",
};

export function categoryWords(category: string | null | undefined): string {
  if (!category) return "not classified";
  return CATEGORY_WORDS[category] ?? category.replace(/_/g, " ");
}

/** One sentence on where a cost figure comes from, so no number is mistaken for a bill or a benchmark. */
export function costBasisNote(basis: "simulated" | "calculated" | undefined, priceChecked?: string): string {
  if (basis === "simulated") return "Simulated: demo mode scripts the token counts, so these costs are not real.";
  return `Calculated from actual token usage and the price list checked ${priceChecked ?? "recently"}. `
    + "Your OpenAI bill can differ slightly.";
}
