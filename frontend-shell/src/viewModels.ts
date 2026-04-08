export type ViewId = "chat" | "settings";

export type RunLogEntry = {
  id: string;
  kind: "plan" | "read" | "search" | "exec" | "edit" | "test" | "reply" | "approval" | "error" | "status";
  status: "started" | "completed" | "error";
  title: string;
  detail: string;
  target: string;
  metadata: Record<string, unknown>;
};

export type RunStageSummary = {
  total: number;
  completed: number;
  running: number;
  error: number;
};

export type ProcessDetailState = {
  streamingReply: string;
  pendingTokenId: string | null;
  approvalText: string;
  currentStatus: string;
};

export type RunCardState = {
  id: string;
  anchorUserTurnIndex: number;
  approvalTokenId: string | null;
  capabilityName: string;
  phaseLabel: string;
  status: "running" | "completed" | "error";
  entries: RunLogEntry[];
  collapsed: boolean;
  summary: string;
  error: {
    code: string;
    message: string;
    detail: string | null;
    stage: string | null;
    retriable: boolean;
    suggestion: string | null;
    trace_id?: string | null;
    context?: Record<string, unknown> | null;
  } | null;
};

export type ChatItem =
  | { id: string; kind: "message"; role: string; content: string }
  | { id: string; kind: "run"; run: RunCardState };

export type ThreadSummary = {
  id: string;
  title: string;
  subtitle: string;
  active: boolean;
};
