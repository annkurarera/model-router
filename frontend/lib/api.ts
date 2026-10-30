import type { ChatResponse, Health, ModelOption, Mode, RouterErrorBody, Stats } from "./types";

export const ROUTER_URL = (process.env.NEXT_PUBLIC_ROUTER_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, public body: RouterErrorBody) {
    super(body.message);
  }
}

// Longer than the router's own 110 s request deadline, so the router always answers first.
async function request<T>(path: string, init?: RequestInit, timeoutMs = 120_000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${ROUTER_URL}${path}`, { ...init, signal: controller.signal });
  } catch {
    throw new ApiError(0, {
      code: "router_unreachable",
      message: `Can't reach the router at ${ROUTER_URL}. Start it with: python -m uvicorn app.main:app --port 8000`,
    });
  } finally {
    clearTimeout(timer);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(res.status, body.error ?? { code: `http_${res.status}`, message: res.statusText });
  }
  return body as T;
}

/** Sent with every playground question: short answers read better and cost less. */
export const STYLE_HINT = "Answer briefly. Use plain text for maths, not LaTeX. Use Markdown only for lists, tables and code.";

function messages(prompt: string) {
  return [{ role: "system", content: STYLE_HINT }, { role: "user", content: prompt }];
}

export function routeRequest(prompt: string, mode: Mode, maxCostUsd: number | null): Promise<ChatResponse> {
  const router: Record<string, unknown> = { mode };
  if (maxCostUsd !== null) router.max_cost_usd = maxCostUsd;
  return request<ChatResponse>("/v1/chat/completions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: "auto", messages: messages(prompt), router }),
  });
}

/** Same question on the reference (strongest) model; the only model a public caller may ask for by name. */
export function compareWithReference(prompt: string): Promise<ChatResponse> {
  return request<ChatResponse>("/v1/chat/completions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: "auto", messages: messages(prompt), router: { compare_with_reference: true } }),
  });
}

export const getHealth = () => request<Health>("/api/health", undefined, 10_000);
export const getModels = () => request<{ price_checked: string; options: ModelOption[] }>("/api/models");
export const getStats = () => request<Stats>("/api/stats");
