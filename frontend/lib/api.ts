const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export interface Provider {
  id: string;
  label: string;
  default_model: string;
  requires_api_key: boolean;
  configured: boolean;
}

export interface ProviderModels {
  provider: string;
  default_model: string;
  models: string[];
}

export interface Conversation {
  id: string;
  created_at: string;
  status: "active" | "cancelled";
  title: string | null;
  provider: string | null;
  model: string | null;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  sequence_number: number;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface DashboardGroup {
  model: string;
  provider: string;
  request_count: number;
  avg_latency_ms: number;
  error_rate: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface DashboardLog {
  id: string;
  model: string;
  provider: string;
  conversation_id: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  status: "success" | "error";
  error_message: string | null;
  created_at: string;
}

export interface Dashboard {
  window_start: string;
  window_end: string;
  summary: {
    request_count: number;
    avg_latency_ms: number;
    error_rate: number;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
  };
  groups: DashboardGroup[];
  latency_points: Array<{
    timestamp: string;
    model: string;
    provider: string;
    avg_latency_ms: number;
  }>;
  logs: DashboardLog[];
}

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new ApiError(
      typeof detail === "string" ? detail : "The request could not be completed.",
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export type ChatOptions = {
  message: string;
  provider?: string;
  model?: string;
  api_key?: string;
};

type StreamHandlers = {
  onConversation: (conversation: Conversation) => void;
  onDelta: (content: string) => void;
};

async function streamChat(
  path: string,
  options: ChatOptions,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(options),
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      typeof body?.detail === "string" ? body.detail : "The request could not be completed.",
      response.status,
    );
  }
  if (!response.body) throw new ApiError("Streaming is not supported by this browser.", 0);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let event = "message";

  const handleLine = (line: string) => {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
      return;
    }
    if (!line.startsWith("data:")) return;
    const payload = JSON.parse(line.slice(5).trim()) as Record<string, unknown>;
    if (event === "conversation") handlers.onConversation(payload as unknown as Conversation);
    if (event === "delta" && typeof payload.content === "string") handlers.onDelta(payload.content);
    if (event === "error") {
      throw new ApiError(
        typeof payload.detail === "string" ? payload.detail : "The stream failed.",
        502,
      );
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    pending += decoder.decode(value, { stream: !done });
    const lines = pending.split("\n");
    pending = lines.pop() ?? "";
    for (const line of lines) handleLine(line);
    if (done) break;
  }
}

export const api = {
  getProviders: () => request<{ providers: Provider[] }>("/providers"),
  getProviderModels: (providerId: string, apiKey?: string) =>
    request<ProviderModels>(`/providers/${encodeURIComponent(providerId)}/models`, {
      headers: apiKey ? { "X-Provider-Api-Key": apiKey } : undefined,
    }),
  listConversations: () => request<Conversation[]>("/conversations"),
  getConversation: (id: string) => request<ConversationDetail>(`/conversations/${id}`),
  createConversation: (options: ChatOptions) =>
    request<ConversationDetail>("/conversations", {
      method: "POST",
      body: JSON.stringify(options),
    }),
  sendMessage: (id: string, options: ChatOptions) =>
    request<ConversationDetail>(`/conversations/${id}/messages`, {
      method: "POST",
      body: JSON.stringify(options),
    }),
  createConversationStream: (
    options: ChatOptions,
    handlers: StreamHandlers,
    signal?: AbortSignal,
  ) => streamChat("/conversations/stream", options, handlers, signal),
  sendMessageStream: (
    id: string,
    options: ChatOptions,
    handlers: StreamHandlers,
    signal?: AbortSignal,
  ) => streamChat(`/conversations/${id}/messages/stream`, options, handlers, signal),
  cancelConversation: (id: string) =>
    request<Conversation>(`/conversations/${id}/cancel`, { method: "POST" }),
  deleteConversation: (id: string) =>
    request<void>(`/conversations/${id}`, { method: "DELETE" }),
  getDashboard: (windowMinutes: number) =>
    request<Dashboard>(`/dashboard?window_minutes=${windowMinutes}`),
};
