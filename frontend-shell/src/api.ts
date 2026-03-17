import type { AssistantResponse, ConfigResponse, ModelsResponse, SessionResponse } from "./types";

const API_BASE = import.meta.env.VITE_ASSISTANT_API_BASE || "";

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
