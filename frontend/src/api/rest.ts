/** REST client — M3 会话/模型/历史 */

const json = async (res: Response) => {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
};

export type SessionRow = {
  id: string;
  title: string;
  status: string;
  model_id: number | null;
  created_at: string;
  updated_at: string;
};

export type ModelRow = {
  id: number;
  provider_id: number;
  provider_slug?: string | null;
  provider_name?: string | null;
  model_id: string;
  display_name: string;
  context_window: number;
  temperature: number;
};

export type ProviderRow = {
  id: number;
  provider_id: string;
  display_name: string;
  base_url: string;
  api_key_set: boolean;
};

export type HistoryMessage = {
  id: number;
  public_id: string;
  role: string;
  content: { text?: string; tool_calls?: unknown } | string;
  tool_call_id?: string | null;
};

export const rest = {
  sessions: () => fetch("/api/sessions").then(json) as Promise<SessionRow[]>,
  createSession: (title?: string, model_id?: number | null) =>
    fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title || "New Session", model_id: model_id ?? null }),
    }).then(json) as Promise<SessionRow>,
  patchSession: (id: string, body: Partial<Pick<SessionRow, "title" | "model_id" | "status">>) =>
    fetch(`/api/sessions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json) as Promise<SessionRow>,
  deleteSession: (id: string) => fetch(`/api/sessions/${id}`, { method: "DELETE" }).then(json),
  messages: (id: string) => fetch(`/api/sessions/${id}/messages`).then(json) as Promise<HistoryMessage[]>,
  providers: () => fetch("/api/providers").then(json) as Promise<ProviderRow[]>,
  createProvider: (body: { provider_id: string; display_name: string; base_url: string; api_key: string }) =>
    fetch("/api/providers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json) as Promise<ProviderRow>,
  testProvider: (id: number, model_id?: string) =>
    fetch(`/api/providers/${id}/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_id: model_id || null }),
    }).then(json) as Promise<{ ok: boolean; error?: string; reply?: string }>,
  createModel: (providerId: number, body: { model_id: string; display_name: string; context_window?: number; temperature?: number }) =>
    fetch(`/api/providers/${providerId}/models`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json) as Promise<ModelRow>,
  models: () => fetch("/api/models").then(json) as Promise<ModelRow[]>,
  resolvedDefault: () => fetch("/api/models/resolved-default").then(json) as Promise<{ model: ModelRow | null }>,
  getDefaultModel: () => fetch("/api/config/default-model").then(json) as Promise<{ default_model_id: number | null }>,
  putDefaultModel: (default_model_id: number | null) =>
    fetch("/api/config/default-model", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ default_model_id }),
    }).then(json),
  deleteProvider: (id: number) => fetch(`/api/providers/${id}`, { method: "DELETE" }).then(json),
  deleteModel: (id: number) => fetch(`/api/models/${id}`, { method: "DELETE" }).then(json),
};

export function historyToChat(rows: HistoryMessage[]): { id: string; role: string; content: string }[] {
  return rows
    .filter((r) => r.role === "user" || r.role === "assistant")
    .map((r) => {
      const text = typeof r.content === "string" ? r.content : r.content?.text || "";
      return { id: r.public_id, role: r.role, content: text };
    });
}
