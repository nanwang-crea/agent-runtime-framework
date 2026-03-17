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
  approval_request: ApprovalRequest | null;
  resume_token_id: string | null;
  session: SessionPayload;
  plan_history: PlanPayload[];
  workspace: string;
};

export type SessionResponse = {
  workspace: string;
  session: SessionPayload;
  plan_history: PlanPayload[];
};
