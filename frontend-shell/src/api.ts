import type {
  AssistantError,
  AssistantResponse,
  ContextPayload,
  ExecutionTraceStep,
  MemoryPayload,
  ModelCenterResponse,
  SessionResponse,
} from "./types";

const EMPTY_SESSION: SessionResponse["session"] = {
  session_id: null,
  turns: [],
};

const EMPTY_MEMORY: AssistantResponse["memory"] = {
  focused_resource: null,
  recent_resources: [],
  last_summary: null,
  active_capability: null,
};

const EMPTY_CONTEXT: ContextPayload = {
  active_workspace: "",
  available_workspaces: [],
};

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

function normalizeAssistantResponse(payload: unknown): AssistantResponse {
  const data = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  return {
    status: typeof data.status === "string" ? data.status : "completed",
    final_answer: typeof data.final_answer === "string" ? data.final_answer : "",
    execution_trace: Array.isArray(data.execution_trace) ? (data.execution_trace as ExecutionTraceStep[]) : [],
    approval_request:
      data.approval_request && typeof data.approval_request === "object"
        ? (data.approval_request as AssistantResponse["approval_request"])
        : null,
    resume_token_id: typeof data.resume_token_id === "string" ? data.resume_token_id : null,
    session:
      data.session && typeof data.session === "object"
        ? (data.session as AssistantResponse["session"])
        : EMPTY_SESSION,
    plan_history: Array.isArray(data.plan_history) ? (data.plan_history as AssistantResponse["plan_history"]) : [],
    memory:
      data.memory && typeof data.memory === "object"
        ? (data.memory as AssistantResponse["memory"])
        : EMPTY_MEMORY,
    context:
      data.context && typeof data.context === "object"
        ? (data.context as ContextPayload)
        : EMPTY_CONTEXT,
    error:
      data.error && typeof data.error === "object"
        ? (data.error as AssistantError)
        : null,
    workspace: typeof data.workspace === "string" ? data.workspace : "",
  };
}

export function fetchSession(): Promise<SessionResponse> {
  return request<SessionResponse>("/api/session");
}

export function updateContext(payload: { workspace?: string }): Promise<SessionResponse> {
  return request<SessionResponse>("/api/context", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function sendMessage(message: string): Promise<AssistantResponse> {
  return request<unknown>("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  }).then(normalizeAssistantResponse);
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
  signal?: AbortSignal,
): Promise<AssistantResponse | null> {
  const response = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    signal,
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
    if (signal?.aborted) {
      reader.cancel();
      break;
    }
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
        finalPayload = normalizeAssistantResponse(payload.payload);
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
  return request<unknown>("/api/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token_id: tokenId, approved }),
  }).then(normalizeAssistantResponse);
}

export function replayRun(runId: string): Promise<AssistantResponse> {
  return request<unknown>("/api/replay", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId }),
  }).then(normalizeAssistantResponse);
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
