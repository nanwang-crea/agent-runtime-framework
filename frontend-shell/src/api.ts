import type {
  AssistantError,
  AssistantResponse,
  ContextPayload,
  ExecutionTraceStep,
  MemoryPayload,
  ModelCenterResponse,
  SessionResponse,
} from "./types";

export class ApiRequestError extends Error {
  status: number;
  assistantError: AssistantError | null;

  constructor(message: string, status: number, assistantError: AssistantError | null = null) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.assistantError = assistantError;
  }
}

/**
 * 后端 API 根地址。
 * - 未设置时：若页面是 file://（如 Electron 打包/dist），则用 http://127.0.0.1:8765，否则用空（相对路径，依赖 Vite 代理）。
 */
const API_BASE = (() => {
  const env = import.meta.env.VITE_ASSISTANT_API_BASE;
  if (env && String(env).trim()) return String(env).trim();
  if (typeof window !== "undefined" && window.location?.protocol === "file:") {
    return "http://127.0.0.1:8765";
  }
  return "";
})();

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let payload: any = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    const assistantError = payload && typeof payload === "object" && payload.error ? (payload.error as AssistantError) : null;
    const traceSuffix = assistantError?.trace_id ? ` [trace_id=${assistantError.trace_id}]` : "";
    const message = assistantError
      ? `${assistantError.code} · ${assistantError.message}${traceSuffix}`
      : `Request failed: ${response.status}`;
    throw new ApiRequestError(message, response.status, assistantError);
  }
  return response.json() as Promise<T>;
}

export function fetchSession(): Promise<SessionResponse> {
  return request<SessionResponse>("/api/session");
}

export function updateContext(payload: { agent_profile?: string; workspace?: string }): Promise<SessionResponse> {
  return request<SessionResponse>("/api/context", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function sendMessage(message: string): Promise<AssistantResponse> {
  return request<AssistantResponse>("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
}

export async function sendMessageStream(
  message: string,
  handlers: {
    onStart?: (event: { message: string }) => void;
    onStatus?: (event: { phase: string; label: string }) => void;
    onDelta?: (event: { delta: string }) => void;
    onStep?: (event: { step: ExecutionTraceStep }) => void;
    onMemory?: (event: { memory: MemoryPayload }) => void;
    onError?: (event: { error: AssistantError }) => void;
    onFinal?: (payload: AssistantResponse) => void;
  },
): Promise<AssistantResponse | null> {
  const response = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!response.ok || !response.body) {
    throw new Error(`Request failed: ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let finalPayload: AssistantResponse | null = null;
  let errorPayload: AssistantError | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const segments = buffer.split(/\r?\n\r?\n/);
    buffer = segments.pop() || "";
    for (const segment of segments) {
      const lines = segment.split(/\r?\n/);
      const eventLine = lines.find((line) => line.startsWith("event:"));
      const dataLine = lines.find((line) => line.startsWith("data:"));
      if (!eventLine || !dataLine) {
        continue;
      }
      const eventName = eventLine.slice(6).trim();
      const payload = JSON.parse(dataLine.slice(5).trim());
      if (eventName === "start") {
        handlers.onStart?.({ message: String(payload.message || "") });
      } else if (eventName === "status") {
        handlers.onStatus?.({
          phase: String(payload.status?.phase || ""),
          label: String(payload.status?.label || ""),
        });
      } else if (eventName === "step") {
        handlers.onStep?.({ step: payload.step as ExecutionTraceStep });
      } else if (eventName === "delta") {
        handlers.onDelta?.({ delta: String(payload.delta || "") });
      } else if (eventName === "memory") {
        handlers.onMemory?.({ memory: payload.memory as MemoryPayload });
      } else if (eventName === "error") {
        errorPayload = payload.error as AssistantError;
        handlers.onError?.({ error: errorPayload });
      } else if (eventName === "final") {
        finalPayload = payload.payload as AssistantResponse;
        handlers.onFinal?.(finalPayload);
      }
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }

  if (finalPayload === null && errorPayload === null) {
    throw new Error("Missing final stream payload");
  }
  return finalPayload;
}

export function respondApproval(tokenId: string, approved: boolean): Promise<AssistantResponse> {
  return request<AssistantResponse>("/api/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token_id: tokenId, approved }),
  });
}

export function fetchModelCenter(): Promise<ModelCenterResponse> {
  return request<ModelCenterResponse>("/api/model-center");
}

export function updateModelCenter(payload: {
  instances?: Record<string, Record<string, unknown>>;
  routes?: Record<string, { instance: string; model: string }>;
}): Promise<ModelCenterResponse> {
  return request<ModelCenterResponse>("/api/model-center", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function runModelCenterAction(payload: { action: string; instance?: string }): Promise<ModelCenterResponse> {
  return request<ModelCenterResponse>("/api/model-center/actions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
