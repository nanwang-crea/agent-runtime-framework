export type Turn = {
  role: string;
  content: string;
};

export type SessionPayload = {
  session_id: string | null;
  turns: Turn[];
};

export type PlanStep = {
  capability_name: string;
  instruction: string;
  status: string;
  observation: string | null;
};

export type PlanPayload = {
  plan_id: string;
  goal: string;
  steps: PlanStep[];
};

export type ApprovalRequest = {
  capability_name: string;
  instruction: string;
  reason: string;
  risk_class: string;
};

export type AssistantResponse = {
  status: string;
  final_answer: string;
  capability_name: string;
  execution_trace: ExecutionTraceStep[];
  approval_request: ApprovalRequest | null;
  resume_token_id: string | null;
  session: SessionPayload;
  plan_history: PlanPayload[];
  workspace: string;
};

export type ExecutionTraceStep = {
  name: string;
  status: string;
  detail: string | null;
};

export type SessionResponse = {
  workspace: string;
  session: SessionPayload;
  plan_history: PlanPayload[];
};

export type ModelProfile = {
  provider: string;
  model_name: string;
  display_name: string;
  supports_chat: boolean;
  supports_tools: boolean;
  supports_vision: boolean;
  context_window: number | null;
  cost_level: string;
  latency_level: string;
  reasoning_level: string;
  recommended_roles: string[];
};

export type ProviderAuthSession = {
  provider: string;
  authenticated: boolean;
  auth_type: string;
  error_message: string | null;
  metadata: Record<string, unknown>;
};

export type ProviderState = {
  provider: string;
  authenticated: boolean;
  auth_session: ProviderAuthSession | null;
  models: ModelProfile[];
};

export type ModelsResponse = {
  providers: ProviderState[];
  routes: Record<string, { provider: string; model_name: string }>;
};

export type ConfigProvider = {
  provider: string;
  api_key_set: boolean;
  api_key_preview: string;
  base_url: string;
};

export type ConfigResponse = {
  path: string;
  providers: ConfigProvider[];
  routes: Record<string, { provider: string; model_name: string }>;
};
