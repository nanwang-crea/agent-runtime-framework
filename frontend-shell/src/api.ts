import type { AssistantResponse, SessionResponse } from "./types";

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
