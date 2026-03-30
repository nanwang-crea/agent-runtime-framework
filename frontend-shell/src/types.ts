export type Turn = {
  role: string;
  content: string;
};

export type SessionPayload = {
  session_id: string | null;
  turns: Turn[];
};

export type AgentProfilePayload = {
  id: string;
  label: string;
  kind: string;
};

export type ContextPayload = {
  active_agent: string;
  available_agents: AgentProfilePayload[];
  active_workspace: string;
  available_workspaces: string[];
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
  trace_id?: string | null;
  context?: Record<string, unknown> | null;
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
  context: ContextPayload;
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
  context: ContextPayload;
};

export type ModelProfile = {
  instance: string;
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

export type InstanceState = {
  instance: string;
  type: string;
  catalog_mode: string;
  authenticated: boolean;
  auth_error: string;
  capabilities: {
    supports_stream: boolean;
    supports_tools: boolean;
    supports_vision: boolean;
    supports_json_mode: boolean;
  };
  models: ModelProfile[];
};

export type ModelsResponse = {
  instances: InstanceState[];
  routes: Record<string, { instance: string; model_name: string }>;
  default_instance: string;
  active_model: { instance: string; model_name: string };
};

export type ConfigInstance = {
  instance: string;
  type: string;
  enabled: boolean;
  api_key_set: boolean;
  api_key_preview: string;
  base_url: string;
};

export type ConfigResponse = {
  path: string;
  instances: ConfigInstance[];
  routes: Record<string, { instance: string; model_name: string }>;
};

export type ModelCenterInstanceConfig = {
  type: string;
  enabled: boolean;
  api_key_set?: boolean;
  api_key_preview?: string;
  connection: Record<string, unknown>;
  credentials: Record<string, unknown>;
  catalog: {
    mode: string;
    models: string[];
  };
};

export type ModelCenterConfig = {
  schema_version: number;
  instances: Record<string, ModelCenterInstanceConfig>;
  routes: Record<string, { instance: string; model: string }>;
};

export type ModelCenterCatalogInstance = {
  type: string;
  enabled: boolean;
  catalog_mode: string;
  authenticated: boolean;
  auth_error: string;
  capabilities: {
    supports_stream: boolean;
    supports_tools: boolean;
    supports_vision: boolean;
    supports_json_mode: boolean;
  };
  models: ModelProfile[];
};

export type ModelCenterResponse = {
  config: ModelCenterConfig;
  runtime: {
    instances: Record<string, ModelCenterCatalogInstance>;
    routes: Record<string, { instance: string; model: string }>;
    default_instance: string;
    active_model: { instance: string; model: string };
  };
  runtime_checks: {
    config_path: string;
  };
};
