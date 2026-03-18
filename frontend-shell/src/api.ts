import type { AssistantResponse, ConfigResponse, ExecutionTraceStep, ModelsResponse, SessionResponse } from "./types";

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
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function fetchSession(): Promise<SessionResponse> {
  return request<SessionResponse>("/api/session");
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
    onDelta?: (event: { delta: string }) => void;
    onStep?: (event: { step: ExecutionTraceStep }) => void;
    onFinal?: (payload: AssistantResponse) => void;
  },
): Promise<AssistantResponse> {
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
      } else if (eventName === "step") {
        handlers.onStep?.({ step: payload.step as ExecutionTraceStep });
      } else if (eventName === "delta") {
        handlers.onDelta?.({ delta: String(payload.delta || "") });
      } else if (eventName === "final") {
        finalPayload = payload.payload as AssistantResponse;
        handlers.onFinal?.(finalPayload);
      }
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }

  if (finalPayload === null) {
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

export function fetchModels(): Promise<ModelsResponse> {
  return request<ModelsResponse>("/api/models");
}

export function authenticateProvider(provider: string, apiKey: string, baseUrl?: string): Promise<ModelsResponse> {
  return request<ModelsResponse>("/api/providers/auth", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider,
      api_key: apiKey,
      base_url: baseUrl || "",
    }),
  });
}

export function selectModel(role: string, provider: string, modelName: string): Promise<ModelsResponse> {
  return request<ModelsResponse>("/api/models/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      role,
      provider,
      model_name: modelName,
    }),
  });
}

export function fetchConfig(): Promise<ConfigResponse> {
  return request<ConfigResponse>("/api/config");
}

export function updateConfig(payload: {
  providers?: Record<string, { api_key?: string; base_url?: string }>;
  routes?: Record<string, { provider: string; model_name: string }>;
}): Promise<{ config: ConfigResponse; models: ModelsResponse }> {
  return request<{ config: ConfigResponse; models: ModelsResponse }>("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
