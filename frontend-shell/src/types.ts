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

export type AssistantError = {
  code: string;
  message: string;
  detail: string | null;
  stage: string | null;
  retriable: boolean;
  suggestion: string | null;
};

export type ResourceMemory = {
  resource_id: string;
  kind: string;
  location: string;
  title: string;
};

export type MemoryPayload = {
  focused_resource: ResourceMemory | null;
  recent_resources: ResourceMemory[];
  last_summary: string | null;
  active_capability: string | null;
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
  memory: MemoryPayload;
  error?: AssistantError | null;
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
  memory: MemoryPayload;
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
  instance: string;
  type: string;
  authenticated: boolean;
  auth_error: string;
  models: ModelProfile[];
};

export type ModelsResponse = {
  providers: ProviderState[];
  routes: Record<string, { instance: string; model_name: string }>;
};

export type ConfigProvider = {
  instance: string;
  type: string;
  enabled: boolean;
  api_key_set: boolean;
  api_key_preview: string;
  base_url: string;
};

export type ConfigResponse = {
  path: string;
  providers: ConfigProvider[];
  routes: Record<string, { instance: string; model_name: string }>;
};

export type ModelCenterProviderConfig = {
  type: string;
  enabled: boolean;
  connection: Record<string, unknown>;
  credentials: Record<string, unknown>;
  auth: {
    mode: string;
    status: string;
    last_error: string;
  };
};

export type ModelCenterConfig = {
  schema_version: number;
  provider_instances: Record<string, ModelCenterProviderConfig>;
  routes: Record<string, { instance: string; model: string }>;
};

export type ModelCenterCatalogProvider = {
  type: string;
  enabled: boolean;
  authenticated: boolean;
  auth_error: string;
  models: ModelProfile[];
};

export type ModelCenterResponse = {
  config: ModelCenterConfig;
  runtime: {
    instances: Record<string, ModelCenterCatalogProvider>;
    routes: Record<string, { instance: string; model: string }>;
  };
  runtime_checks: {
    config_path: string;
  };
};
