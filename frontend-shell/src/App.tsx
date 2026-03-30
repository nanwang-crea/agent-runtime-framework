import { FormEvent, Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiRequestError,
  fetchModelCenter,
  fetchSession,
  replayRun,
  respondApproval,
  runModelCenterAction,
  sendMessageStream,
  updateContext,
  updateModelCenter,
} from "./api";
import { MarkdownContent } from "./components/MarkdownContent";
import type {
  AssistantError,
  AssistantResponse,
  ConfigResponse,
  ContextPayload,
  MemoryPayload,
  ModelCenterResponse,
  ModelsResponse,
  PlanPayload,
  PlanStep,
  SessionPayload,
} from "./types";

const examples = ["你好", "列出当前目录", "读取 README.md", "总结 README.md"];
const routedRoles = ["default", "conversation", "capability_selector", "planner", "interpreter", "resolver", "executor", "composer"];
const views = [
  { id: "chat", label: "Chat" },
  { id: "history", label: "History" },
  { id: "settings", label: "Settings" },
] as const;

type ViewId = (typeof views)[number]["id"];

type RunLogEntry = {
  id: string;
  kind: "status" | "step" | "warning" | "error";
  text: string;
};

type RunStageSummary = {
  total: number;
  completed: number;
  running: number;
  error: number;
};

type ProcessDetailState = {
  streamingReply: string;
  pendingTokenId: string | null;
  approvalText: string;
  currentStatus: string;
};

type RunCardState = {
  id: string;
  anchorUserTurnIndex: number;
  approvalTokenId: string | null;
  capabilityName: string;
  phaseLabel: string;
  status: "running" | "completed" | "error";
  entries: RunLogEntry[];
  collapsed: boolean;
  summary: string;
  error: AssistantError | null;
};

function App() {
  const [workspace, setWorkspace] = useState("");
  const [contextState, setContextState] = useState<ContextPayload>({
    active_agent: "codex",
    available_agents: [],
    active_workspace: "",
    available_workspaces: [],
  });
  const [session, setSession] = useState<SessionPayload>({ session_id: null, turns: [] });
  const [plans, setPlans] = useState<PlanPayload[]>([]);
  const [memory, setMemory] = useState<MemoryPayload>({
    focused_resource: null,
    recent_resources: [],
    last_summary: null,
    active_capability: null,
  });
  const [modelCenter, setModelCenter] = useState<ModelCenterResponse | null>(null);
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState("idle");
  const [runCards, setRunCards] = useState<RunCardState[]>([]);
  const [streamingReply, setStreamingReply] = useState("");
  const [pendingTokenId, setPendingTokenId] = useState<string | null>(null);
  const [approvalText, setApprovalText] = useState("");
  const [activeView, setActiveView] = useState<ViewId>("chat");
  const [pendingUserMessage, setPendingUserMessage] = useState("");
  const [uiError, setUiError] = useState<AssistantError | null>(null);
  const [showJumpToLatestRun, setShowJumpToLatestRun] = useState(false);
  const [instanceDrafts, setInstanceDrafts] = useState<Record<string, { apiKey: string; baseUrl: string }>>({});
  const [globalModelDraft, setGlobalModelDraft] = useState<{ instance: string; model: string }>({ instance: "", model: "" });
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const runCardRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const abortControllerRef = useRef<AbortController | null>(null);

  const handleStop = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setStatus("idle");
  }, []);

  useEffect(() => {
    void loadSession();
    void loadModelCenter();
  }, []);

  async function loadSession() {
    try {
      const payload = await fetchSession();
      setWorkspace(payload.workspace);
      setContextState(payload.context);
      setSession(payload.session);
      setPlans(payload.plan_history);
      setMemory(payload.memory);
      setUiError(null);
    } catch (error) {
      setUiError(extractAssistantError(error, "加载会话失败。"));
      setStatus("error");
    }
  }

  async function loadModelCenter() {
    try {
      const payload = await fetchModelCenter();
      setModelCenter(payload);
      setInstanceDrafts((current) => {
        const next = { ...current };
        for (const [instanceName, instanceCfg] of Object.entries(payload.config.instances || {})) {
          const baseUrl = String((instanceCfg.connection || {})["base_url"] || "");
          if (!next[instanceName]) {
            next[instanceName] = { apiKey: "", baseUrl };
          } else if (!next[instanceName].baseUrl) {
            next[instanceName] = {
              ...next[instanceName],
              baseUrl,
            };
          }
        }
        return next;
      });
      setUiError(null);
    } catch (error) {
      setUiError(extractAssistantError(error, "加载模型配置失败。"));
      setStatus("error");
    }
  }

  const models = useMemo<ModelsResponse>(() => {
    if (!modelCenter) {
      return { instances: [], routes: {}, default_instance: "", active_model: { instance: "", model_name: "" } };
    }
    const instances = Object.entries(modelCenter.runtime.instances || {}).map(([instanceId, state]) => ({
      instance: instanceId,
      type: state.type,
      catalog_mode: state.catalog_mode,
      authenticated: Boolean(state.authenticated),
      auth_error: state.auth_error || "",
      capabilities: state.capabilities,
      models: state.models || [],
    }));
    return {
      instances,
      default_instance: String(modelCenter.runtime.default_instance || ""),
      active_model: {
        instance: String(modelCenter.runtime.active_model?.instance || ""),
        model_name: String(modelCenter.runtime.active_model?.model || ""),
      },
      routes: Object.fromEntries(
        Object.entries(modelCenter.runtime.routes || {}).map(([role, route]) => [
          role,
          { instance: route.instance, model_name: route.model },
        ]),
      ),
    };
  }, [modelCenter]);

  const config = useMemo<ConfigResponse>(() => {
    if (!modelCenter) {
      return { path: "", instances: [], routes: {} };
    }
    const instances = Object.entries(modelCenter.config.instances || {}).map(([instanceId, instanceCfg]) => {
      return {
        instance: instanceId,
        type: instanceCfg.type,
        enabled: Boolean(instanceCfg.enabled),
        api_key_set: Boolean(instanceCfg.api_key_set),
        api_key_preview: String(instanceCfg.api_key_preview || ""),
        base_url: String((instanceCfg.connection || {})["base_url"] || ""),
      };
    });
    const routes = Object.fromEntries(
      Object.entries(modelCenter.config.routes || {}).map(([role, route]) => [
        role,
        { instance: route.instance, model_name: route.model },
      ]),
    );
    return {
      path: String(modelCenter.runtime_checks?.config_path || ""),
      instances,
      routes,
    };
  }, [modelCenter]);

  function applyResponse(payload: AssistantResponse, runId?: string, anchorUserTurnIndex?: number) {
    setWorkspace(payload.workspace);
    setContextState(payload.context);
    setSession(payload.session);
    setPlans(payload.plan_history);
    setMemory(payload.memory);
    setStatus(payload.status);
    setPendingTokenId(payload.resume_token_id);
    setApprovalText(
      payload.approval_request
        ? `${payload.approval_request.reason} | ${payload.approval_request.capability_name} | ${payload.approval_request.instruction}`
        : "",
    );
    if (runId) {
      setRunCards((current) =>
        finalizeRunCard(current, runId, payload, anchorUserTurnIndex),
      );
    }
    setStreamingReply("");
  }

  async function handleReplay(runId: string) {
    try {
      setStatus("running");
      const payload = await replayRun(runId);
      applyResponse(payload);
      setUiError(null);
    } catch (error) {
      setUiError(extractAssistantError(error, "重试运行失败。"));
      setStatus("error");
    }
  }

  async function handleAgentSwitch(agentProfile: string) {
    try {
      const payload = await updateContext({ agent_profile: agentProfile });
      setWorkspace(payload.workspace);
      setContextState(payload.context);
      setSession(payload.session);
      setPlans(payload.plan_history);
      setMemory(payload.memory);
      setPendingTokenId(null);
      setApprovalText("");
      setPendingUserMessage("");
      setStreamingReply("");
      setRunCards([]);
      setStatus("idle");
      setUiError(null);
    } catch (error) {
      setUiError(extractAssistantError(error, "切换 Agent 失败。"));
      setStatus("error");
    }
  }

  async function handleWorkspaceSwitch(nextWorkspace: string) {
    if (!nextWorkspace.trim()) {
      return;
    }
    try {
      const payload = await updateContext({ workspace: nextWorkspace.trim() });
      setWorkspace(payload.workspace);
      setContextState(payload.context);
      setSession(payload.session);
      setPlans(payload.plan_history);
      setMemory(payload.memory);
      setPendingTokenId(null);
      setApprovalText("");
      setPendingUserMessage("");
      setStreamingReply("");
      setRunCards([]);
      setStatus("idle");
      setUiError(null);
    } catch (error) {
      setUiError(extractAssistantError(error, "切换工作区失败。"));
      setStatus("error");
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed) {
      return;
    }
    setStatus("streaming");
    const anchorUserTurnIndex = displayedTurns.filter((turn) => turn.role === "user").length;
    setPendingUserMessage(trimmed);
    setStreamingReply("");
    const runId = `run-${Date.now()}`;
    setMessage("");
    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    try {
      const finalPayload = await sendMessageStream(trimmed, {
        onStart: () => {
          setActiveView("chat");
        },
        onStatus: ({ label }) => {
          setRunCards((current) =>
            upsertRunCard(current, {
              id: runId,
              anchorUserTurnIndex,
              capabilityName: "routing",
                phaseLabel: label || "处理中",
                status: "running",
                summary: "运行中",
                error: null,
                approvalTokenId: null,
              }, (run) => ({
                ...run,
                capabilityName: run.capabilityName === "routing" ? inferCapabilityName(label, run.capabilityName) : run.capabilityName,
                phaseLabel: label || run.phaseLabel,
                entries: appendRunEntry(run.entries, "status", label || "处理中"),
              })),
          );
        },
        onDelta: ({ delta }) => {
          setStreamingReply((current) => current + delta);
        },
        onStep: ({ step }) => {
          setRunCards((current) =>
            upsertRunCard(current, {
              id: runId,
              anchorUserTurnIndex,
              capabilityName: "routing",
                phaseLabel: "处理中",
                status: "running",
                summary: "运行中",
                error: null,
                approvalTokenId: null,
              }, (run) => ({
                ...run,
                capabilityName: inferCapabilityName(step.name, run.capabilityName),
                phaseLabel: normalizeDetail(step.detail) || step.name || run.phaseLabel,
                entries: appendRunEntry(
                  run.entries,
                  step.status === "error" ? "error" : "step",
                formatStepLabel(step),
              ),
            })),
          );
        },
        onMemory: ({ memory: nextMemory }) => {
          setMemory(nextMemory);
        },
        onError: ({ error }) => {
          setStatus("error");
          setRunCards((current) =>
            upsertRunCard(current, {
              id: runId,
              anchorUserTurnIndex,
              capabilityName: "assistant",
              phaseLabel: "请求失败",
              status: "error",
              summary: `${error.code} · ${error.message}`,
              error,
              approvalTokenId: null,
            }, (run) => ({
              ...run,
              status: "error",
              collapsed: false,
              error,
              summary: `${error.code} · ${error.message}`,
              entries: appendRunEntry(run.entries, "error", `${error.code} · ${error.message}`),
            })),
          );
        },
        onFinal: (finalPayload) => {
          applyResponse(finalPayload, runId, anchorUserTurnIndex);
        },
      }, abortController.signal);
      abortControllerRef.current = null;
      if (finalPayload !== null) {
        setPendingUserMessage("");
      }
      setUiError(null);
    } catch (error) {
      abortControllerRef.current = null;
      if (error instanceof DOMException && error.name === "AbortError") {
        setRunCards((current) =>
          current.map((run) =>
            run.id === runId ? { ...run, status: "completed", phaseLabel: "已中止", summary: "用户已停止" } : run,
          ),
        );
        return;
      }
      const message = error instanceof Error ? error.message : "流式请求失败";
      setStatus("error");
      setRunCards((current) =>
        upsertRunCard(current, {
          id: runId,
          anchorUserTurnIndex,
          capabilityName: "assistant",
            phaseLabel: "请求失败",
            status: "error",
            summary: message,
            approvalTokenId: null,
            error: {
              code: "STREAM_BROKEN",
            message,
            detail: message,
            stage: "stream",
            retriable: true,
            suggestion: "可以重试一次；如果持续失败，请检查后端日志。",
          },
        }, (run) => ({
          ...run,
          status: "error",
          collapsed: false,
          error: {
            code: "STREAM_BROKEN",
            message,
            detail: message,
            stage: "stream",
            retriable: true,
            suggestion: "可以重试一次；如果持续失败，请检查后端日志。",
          },
          summary: message,
          entries: appendRunEntry(run.entries, "error", `STREAM_BROKEN · ${message}`),
        })),
      );
    }
  }

  async function handleApproval(approved: boolean) {
    if (!pendingTokenId) {
      return;
    }
    try {
      setStatus("running");
      const targetRun = [...runCards].reverse().find((run) => run.approvalTokenId === pendingTokenId) || null;
      const payload = await respondApproval(pendingTokenId, approved);
      applyResponse(payload, targetRun?.id, targetRun?.anchorUserTurnIndex);
      setUiError(null);
    } catch (error) {
      setUiError(extractAssistantError(error, approved ? "接受请求失败。" : "拒绝请求失败。"));
      setStatus("error");
    }
  }

  function updateDraft(instanceId: string, key: "apiKey" | "baseUrl", value: string) {
    setInstanceDrafts((current) => ({
      ...current,
      [instanceId]: {
        apiKey: current[instanceId]?.apiKey || "",
        baseUrl: current[instanceId]?.baseUrl || "",
        [key]: value,
      },
    }));
  }

  async function handleAuth(instanceId: string) {
    try {
      const draft = instanceDrafts[instanceId] || { apiKey: "", baseUrl: "" };
      const updated = await updateModelCenter({
        instances: {
          [instanceId]: {
            credentials: { api_key: draft.apiKey },
            connection: { base_url: draft.baseUrl },
          },
        },
      });
      setModelCenter(updated);
      const payload = await runModelCenterAction({ action: "authenticate_instance", instance: instanceId });
      setModelCenter(payload);
      updateDraft(instanceId, "apiKey", "");
      setUiError(null);
    } catch (error) {
      setUiError(extractAssistantError(error, `认证实例 ${instanceId} 失败。`));
      setStatus("error");
    }
  }

  async function handleDefaultModelSelect(instanceId: string, modelName: string) {
    if (!instanceId || !modelName) {
      return;
    }
    try {
      const routes = Object.fromEntries(
        routedRoles.map((role) => [role, { instance: instanceId, model: modelName }]),
      );
      const payload = await updateModelCenter({ routes });
      setModelCenter(payload);
      setUiError(null);
    } catch (error) {
      setUiError(extractAssistantError(error, `切换默认模型到 ${instanceId}/${modelName} 失败。`));
      setStatus("error");
    }
  }

  function preferredModelForInstance(instanceId: string): string {
    const instanceState = models.instances.find((item) => item.instance === instanceId);
    if (!instanceState) {
      return "";
    }
    const routed = routedRoles
      .map((role) => models.routes[role])
      .find((route) => route?.instance === instanceId);
    return routed?.model_name || instanceState.models[0]?.model_name || "";
  }

  async function handleSaveConfig(instanceId: string) {
    try {
      const draft = instanceDrafts[instanceId] || { apiKey: "", baseUrl: "" };
      const payload = await updateModelCenter(
        {
          instances: {
            [instanceId]: {
              credentials: { api_key: draft.apiKey },
              connection: { base_url: draft.baseUrl },
            },
          },
        },
      );
      setModelCenter(payload);
      updateDraft(instanceId, "apiKey", "");
      setUiError(null);
    } catch (error) {
      setUiError(extractAssistantError(error, `保存实例 ${instanceId} 配置失败。`));
      setStatus("error");
    }
  }

  const selectedGlobalInstance = models.active_model.instance || models.default_instance || models.instances[0]?.instance || "";
  const selectedGlobalInstanceState = models.instances.find((item) => item.instance === selectedGlobalInstance) || models.instances[0];
  const selectedGlobalModel = models.active_model.model_name || selectedGlobalInstanceState?.models[0]?.model_name || "";
  const readyInstanceCount = models.instances.filter((item) => item.authenticated).length;
  const selectedGlobalBaseUrl =
    config.instances.find((item) => item.instance === selectedGlobalInstance)?.base_url || "未配置";

  useEffect(() => {
    setGlobalModelDraft((current) => {
      if (
        current.instance === selectedGlobalInstance &&
        current.model === selectedGlobalModel
      ) {
        return current;
      }
      return {
        instance: selectedGlobalInstance,
        model: selectedGlobalModel,
      };
    });
  }, [selectedGlobalInstance, selectedGlobalModel]);

  const displayedTurns = useMemo(() => {
    const turns = [...session.turns];
    const hasCommittedPendingUser = turns.some(
      (turn, index) => turn.role === "user" && turn.content === pendingUserMessage && index >= Math.max(0, turns.length - 2),
    );
    if (pendingUserMessage && !hasCommittedPendingUser) {
      turns.push({ role: "user", content: pendingUserMessage });
    }
    const latestTurn = turns[turns.length - 1];
    const hasCommittedAssistant =
      latestTurn?.role === "assistant" &&
      (latestTurn.content === streamingReply ||
        latestTurn.content.startsWith(streamingReply) ||
        streamingReply.startsWith(latestTurn.content));
    if (streamingReply && !hasCommittedAssistant) {
      turns.push({ role: "assistant", content: streamingReply });
    }
    return turns;
  }, [pendingUserMessage, session.turns, streamingReply]);

  const runsByAnchor = useMemo(() => {
    const grouped: Record<number, RunCardState[]> = {};
    for (const run of runCards) {
      const key = run.anchorUserTurnIndex;
      if (!grouped[key]) {
        grouped[key] = [];
      }
      grouped[key].push(run);
    }
    return grouped;
  }, [runCards]);

  const chatItems = useMemo(() => {
    const items: Array<
      | { id: string; kind: "message"; role: string; content: string }
      | { id: string; kind: "run"; run: RunCardState }
    > = [];
    let userIndex = 0;

    for (let index = 0; index < displayedTurns.length; index += 1) {
      const turn = displayedTurns[index];
      items.push({
        id: `message-${index}-${turn.role}`,
        kind: "message",
        role: turn.role,
        content: turn.content,
      });
      if (turn.role === "user") {
        const runsForTurn = runsByAnchor[userIndex] || [];
        for (const run of runsForTurn) {
          items.push({
            id: `run-${run.id}`,
            kind: "run",
            run,
          });
        }
        userIndex += 1;
      }
    }

    return items;
  }, [displayedTurns, runsByAnchor]);

  const activeWorkspace = contextState.active_workspace || workspace;

  const latestRunCardId = runCards.length > 0 ? runCards[runCards.length - 1].id : null;
  const latestRun = runCards.length > 0 ? runCards[runCards.length - 1] : null;
  const activeRun = [...runCards].reverse().find((run) => run.status === "running") || latestRun;

  const runStageSummary = useMemo<RunStageSummary>(() => {
    if (!activeRun) {
      return { total: 0, completed: 0, running: 0, error: 0 };
    }

    let total = 0;
    let error = 0;

    for (const entry of activeRun.entries) {
      if (entry.kind === "step" || entry.kind === "error") {
        total += 1;
      }
      if (entry.kind === "error") {
        error += 1;
      }
    }

    const running = activeRun.status === "running" && total > error ? 1 : 0;
    const completed = Math.max(total - error - running, 0);
    return { total, completed, running, error };
  }, [activeRun]);

  const isBusy = status === "running" || status === "streaming";

  function refreshLatestRunVisibility() {
    if (activeView !== "chat") {
      setShowJumpToLatestRun(false);
      return;
    }
    if (!latestRunCardId) {
      setShowJumpToLatestRun(false);
      return;
    }
    const container = messagesRef.current;
    const latestRunElement = runCardRefs.current[latestRunCardId];
    if (!container || !latestRunElement) {
      setShowJumpToLatestRun(false);
      return;
    }
    const viewTop = container.scrollTop;
    const viewBottom = viewTop + container.clientHeight;
    const cardTop = latestRunElement.offsetTop;
    const cardBottom = cardTop + latestRunElement.offsetHeight;
    const isVisible = cardBottom > viewTop && cardTop < viewBottom;
    setShowJumpToLatestRun(!isVisible);
  }

  function handleMessagesScroll() {
    refreshLatestRunVisibility();
  }

  function handleJumpToLatestRun() {
    if (!latestRunCardId) {
      return;
    }
    const latestRunElement = runCardRefs.current[latestRunCardId];
    if (!latestRunElement) {
      return;
    }
    latestRunElement.scrollIntoView({ behavior: "smooth", block: "center" });
    setShowJumpToLatestRun(false);
  }

  useEffect(() => {
    if (activeView !== "chat" || !messagesRef.current) {
      return;
    }
    messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
  }, [activeView, chatItems, streamingReply]);

  useEffect(() => {
    refreshLatestRunVisibility();
  }, [activeView, chatItems, latestRunCardId, streamingReply]);

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <p className="kicker">Agent Runtime Framework</p>
          <h1>Agent Shell</h1>
          <p className="brand-copy">同一个会话里切换 agent 和 workspace，让对话流承载执行过程、历史和配置中心。</p>
        </div>

        <nav className="nav">
          {views.map((view) => (
            <button
              key={view.id}
              type="button"
              className={`nav-item ${activeView === view.id ? "active" : ""}`}
              onClick={() => setActiveView(view.id)}
            >
              {view.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-card">
          <span>当前工作区</span>
          <code>{activeWorkspace || "加载中..."}</code>
        </div>

        <div className="sidebar-card">
          <span>切换 Agent</span>
          <select value={contextState.active_agent} onChange={(event) => void handleAgentSwitch(event.target.value)}>
            {contextState.available_agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.label}
              </option>
            ))}
          </select>
        </div>

        <div className="sidebar-card">
          <span>切换工作区</span>
          <select value={contextState.active_workspace || workspace} onChange={(event) => void handleWorkspaceSwitch(event.target.value)}>
            {(contextState.available_workspaces.length > 0 ? contextState.available_workspaces : [workspace]).filter(Boolean).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </div>

        <div className="sidebar-stats">
          <div className="stat-card">
            <span>轮次</span>
            <strong>{session.turns.length}</strong>
          </div>
          <div className="stat-card">
            <span>运行次数</span>
            <strong>{runCards.length}</strong>
          </div>
          <div className="stat-card">
            <span>状态</span>
            <strong>{status}</strong>
          </div>
        </div>

      </aside>

      <section className="main-stage">
        <header className="topbar">
          <div>
            <p className="eyebrow">{activeView === "chat" ? "对话工作区" : activeView === "history" ? "运行历史" : "模型与配置"}</p>
            <h2>{activeView === "chat" ? "Agent Shell" : activeView === "history" ? "历史" : "设置"}</h2>
          </div>
          <div className="topbar-meta">
            <span className="pill">{contextState.active_agent}</span>
            <span className="pill">{compactText(activeWorkspace || "workspace", 28)}</span>
            <span className={`pill ${status}`}>{status}</span>
          </div>
        </header>

        {uiError ? (
          <section className="panel">
            <div className="run-error-card">
              <strong>{uiError.code} · {uiError.message}</strong>
              {uiError.suggestion ? <p>{uiError.suggestion}</p> : null}
              <code>
                {uiError.stage ? `${uiError.stage} · ` : ""}
                {uiError.trace_id ? `trace_id=${uiError.trace_id}` : "trace_id=unknown"}
              </code>
            </div>
          </section>
        ) : null}

        {activeView === "chat" ? (
          <section className="chat-layout">
            <section className="panel conversation-panel">
              <div className="panel-head conversation-head">
                <div>
                  <h3>对话</h3>
                  <p className="panel-subcopy">把 Agent 过程收进对话流里：默认只显示简短状态和少量步骤，需要时再展开细节。</p>
                </div>
                <div className="live-shell-meta">
                  <span className={`pill ${status}`}>{status === "streaming" ? "live" : status}</span>
                  <span className="pill">{contextState.active_agent}</span>
                  <span className="pill">{runCards.length} runs</span>
                </div>
              </div>

              <div className="conversation-intro">
                <div className="conversation-intro-copy">
                  <strong>{activeWorkspace ? compactText(activeWorkspace, 56) : "当前工作区"}</strong>
                  <span>对话是主视图；流程、审批、记忆和计划摘要都以内联方式跟随消息出现。</span>
                </div>
                <div className="example-row compact">
                  {examples.map((item) => (
                    <button key={item} type="button" className="ghost" onClick={() => setMessage(item)}>
                      {item}
                    </button>
                  ))}
                </div>
              </div>

              <div ref={messagesRef} className="messages" onScroll={handleMessagesScroll}>
                {chatItems.length === 0 ? (
                  <div className="empty-state conversation-empty-state">
                    <strong>开始一段对话</strong>
                    <p>发送消息后，系统会先在消息下方显示一个轻量流程块，再自然过渡到 assistant 的最终回答。</p>
                  </div>
                ) : (
                  chatItems.map((item) => (
                    <Fragment key={item.id}>
                      {item.kind === "message" ? (
                        <div className={`message ${item.role} ${item.role === "assistant" && item.content === streamingReply ? "streaming" : ""}`}>
                          <small>{item.role === "user" ? "You" : "Assistant"}</small>
                          {item.role === "assistant" ? <MarkdownContent content={item.content} /> : <div>{item.content}</div>}
                        </div>
                      ) : (
                        <RunCard
                          run={item.run}
                          setContainerRef={(element) => {
                            runCardRefs.current[item.run.id] = element;
                          }}
                          onToggle={() =>
                            setRunCards((current) =>
                              current.map((run) =>
                                run.id !== item.run.id
                                  ? run
                                  : {
                                      ...run,
                                      collapsed: !run.collapsed,
                                    },
                              ),
                            )
                          }
                          stageSummary={item.run.id === activeRun?.id ? runStageSummary : summarizeRunEntries(item.run.entries, item.run.status)}
                          processDetails={item.run.id === latestRunCardId ? {
                            streamingReply,
                            pendingTokenId,
                            approvalText,
                            currentStatus: status,
                          } : null}
                          onApproval={(approved) => void handleApproval(approved)}
                          onReplay={() => void handleReplay(item.run.id)}
                        />
                      )}
                    </Fragment>
                  ))
                )}
              </div>

              {showJumpToLatestRun ? (
                <div className="run-jump-wrap">
                  <button type="button" className="ghost" onClick={handleJumpToLatestRun}>
                    跳到最新流程
                  </button>
                </div>
              ) : null}

              <form className="composer" onSubmit={handleSubmit}>
                <textarea
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  placeholder="输入消息，支持正常聊天，也支持列目录、读取文件、总结文档"
                  disabled={isBusy}
                />
                <div className="composer-bar">
                  <span className="composer-hint">过程状态会内联显示在对话里，最终回复仍然保持视觉主导。</span>
                  {(status === "streaming" || status === "running") ? (
                    <button type="button" className="stop-btn" onClick={handleStop}>
                      停止
                    </button>
                  ) : (
                    <button type="submit" className="primary" disabled={isBusy || !message.trim()}>
                      发送
                    </button>
                  )}
                </div>
              </form>
            </section>
          </section>
        ) : null}

        {activeView === "history" ? (
          <section className="history-layout">
            <section className="panel history-panel">
              <div className="panel-head">
                <h3>聊天历史</h3>
              </div>
              <div className="history-list">
                {session.turns.length === 0 ? (
                  <div className="empty-state">
                    <strong>暂无聊天历史</strong>
                    <p>发送消息后，这里会按时间顺序展示对话记录。</p>
                  </div>
                ) : (
                  session.turns.map((turn, index) => (
                    <div key={`${turn.role}-${index}`} className="history-item">
                      <span className={`history-role ${turn.role}`}>{turn.role}</span>
                      <p>{compactText(turn.content, 220)}</p>
                    </div>
                  ))
                )}
              </div>
            </section>

            <section className="panel history-panel">
              <div className="panel-head">
                <h3>计划时间线</h3>
              </div>
              <div className="timeline">
                {plans.length === 0 ? (
                  <div className="empty-state">
                    <strong>暂无计划历史</strong>
                    <p>当 assistant 执行桌面任务时，这里会记录计划和步骤结果。</p>
                  </div>
                ) : (
                  [...plans].reverse().map((plan) => (
                    <div key={plan.plan_id} className="timeline-card">
                      <h3>{plan.goal}</h3>
                      {plan.steps.map((step: PlanStep, index: number) => (
                        <div key={`${plan.plan_id}-${index}`} className="timeline-step">
                          <span className="step-status">{step.status}</span>
                          <strong>{step.capability_name}</strong>
                          <p>{compactText(step.instruction, 220)}</p>
                          {step.observation ? <code>{compactText(step.observation, 240)}</code> : null}
                        </div>
                      ))}
                    </div>
                  ))
                )}
              </div>
            </section>
          </section>
        ) : null}

        {activeView === "settings" ? (
          <section className="settings-layout">
            <section className="panel settings-panel">
              <div className="panel-head">
                <h3>模型控制台</h3>
              </div>
              <div className="model-console-hero">
                <div className="hero-copy">
                  <span className="eyebrow">Global Runtime</span>
                  <h3>{selectedGlobalModel || "未选择模型"}</h3>
                  <p>当前界面只保留一个全局生效模型。被选中的实例和模型会统一驱动对话、规划和内部解析流程，其他实例只作为候选资源存在。</p>
                </div>
                <div className="hero-metrics">
                  <div className="metric-tile">
                    <span>当前实例</span>
                    <strong>{selectedGlobalInstance || "无"}</strong>
                  </div>
                  <div className="metric-tile">
                    <span>可用实例</span>
                    <strong>{readyInstanceCount}/{models.instances.length}</strong>
                  </div>
                  <div className="metric-tile">
                    <span>路由范围</span>
                    <strong>全局</strong>
                  </div>
                </div>
              </div>

              <div className="settings-card featured-model-card">
                <div className="instance-head">
                  <strong>当前生效模型</strong>
                  <span className="pill ready">单一全局模型</span>
                </div>
                <p className="instance-meta">选择草稿后显式应用，避免误操作。当前 endpoint：{selectedGlobalBaseUrl}</p>
                <div className="form-pair">
                  <select
                    value={globalModelDraft.instance}
                    onChange={(event) => {
                      const nextInstance = models.instances.find((item) => item.instance === event.target.value);
                      setGlobalModelDraft({
                        instance: event.target.value,
                        model: nextInstance?.models[0]?.model_name || "",
                      });
                    }}
                  >
                    <option value="">选择实例</option>
                    {models.instances.map((item) => (
                      <option key={item.instance} value={item.instance}>
                        {item.instance}
                      </option>
                    ))}
                  </select>
                  <select
                    value={globalModelDraft.model}
                    onChange={(event) => setGlobalModelDraft((current) => ({ ...current, model: event.target.value }))}
                  >
                    <option value="">选择模型</option>
                    {(models.instances.find((item) => item.instance === globalModelDraft.instance)?.models || []).map((model) => (
                      <option key={`${globalModelDraft.instance}-${model.model_name}`} value={model.model_name}>
                        {model.display_name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="action-row">
                  <button
                    type="button"
                    className="primary"
                    onClick={() => void handleDefaultModelSelect(globalModelDraft.instance, globalModelDraft.model)}
                  >
                    应用为全局模型
                  </button>
                  <span className="inline-hint">
                    当前已生效：{selectedGlobalInstance || "未选择实例"} / {selectedGlobalModel || "未选择模型"}
                  </span>
                </div>
              </div>

              <div className="config-summary compact">
                <span>配置文件路径</span>
                <code>{config.path || "加载中..."}</code>
              </div>

              <div className="settings-grid">
                {config.instances.map((instanceConfig) => {
                  const draft = instanceDrafts[instanceConfig.instance] || { apiKey: "", baseUrl: instanceConfig.base_url || "" };
                  const runtimeInstance = models.instances.find((item) => item.instance === instanceConfig.instance);
                  return (
                    <div key={`config-${instanceConfig.instance}`} className="settings-card instance-card">
                      <div className="instance-head">
                        <div>
                          <strong>{instanceConfig.instance}</strong>
                          <p className="instance-type">{instanceConfig.type}</p>
                        </div>
                        <span className={`pill ${runtimeInstance?.authenticated ? "ready" : "idle"}`}>
                          {runtimeInstance?.authenticated ? "authenticated" : (instanceConfig.api_key_set ? instanceConfig.api_key_preview : "not configured")}
                        </span>
                      </div>
                      <p className="instance-meta">
                        {instanceConfig.type} · catalog: {runtimeInstance?.catalog_mode || "static"} · {runtimeInstance?.auth_error || `base URL: ${instanceConfig.base_url || "未配置"}`}
                      </p>
                      <div className="instance-capabilities">
                        <span className={`mini-pill ${runtimeInstance?.capabilities.supports_stream ? "on" : ""}`}>stream</span>
                        <span className={`mini-pill ${runtimeInstance?.capabilities.supports_tools ? "on" : ""}`}>tools</span>
                        <span className={`mini-pill ${runtimeInstance?.capabilities.supports_vision ? "on" : ""}`}>vision</span>
                        <span className={`mini-pill ${runtimeInstance?.capabilities.supports_json_mode ? "on" : ""}`}>json</span>
                      </div>
                      <div className="form-pair">
                        <input
                          value={draft.apiKey}
                          onChange={(event) => updateDraft(instanceConfig.instance, "apiKey", event.target.value)}
                          placeholder={`${instanceConfig.instance} API key`}
                        />
                        <input
                          value={draft.baseUrl}
                          onChange={(event) => updateDraft(instanceConfig.instance, "baseUrl", event.target.value)}
                          placeholder={instanceConfig.base_url || "base URL"}
                        />
                      </div>
                      <div className="action-row">
                        <button
                          type="button"
                          className="primary"
                          onClick={() => void handleSaveConfig(instanceConfig.instance)}
                        >
                          保存实例
                        </button>
                        <button type="button" className="ghost" onClick={() => void handleAuth(instanceConfig.instance)}>
                          认证
                        </button>
                        <button
                          type="button"
                          className="ghost"
                          onClick={() => void handleDefaultModelSelect(instanceConfig.instance, preferredModelForInstance(instanceConfig.instance))}
                        >
                          设为当前模型
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          </section>
        ) : null}
      </section>
    </main>
  );
}

function appendRunEntry(entries: RunLogEntry[], kind: RunLogEntry["kind"], text: string): RunLogEntry[] {
  if (!text.trim()) {
    return entries;
  }
  const previous = entries[entries.length - 1];
  if (previous && previous.kind === kind && previous.text === text) {
    return entries;
  }
  return [...entries, { id: `${kind}-${entries.length}-${text}`, kind, text }];
}

function upsertRunCard(
  runs: RunCardState[],
  base: Omit<RunCardState, "entries" | "collapsed">,
  update: (run: RunCardState) => RunCardState,
): RunCardState[] {
  const existing = runs.find((run) => run.id === base.id);
  if (existing) {
    return runs.map((run) => (run.id === base.id ? update(run) : run));
  }
  const created: RunCardState = {
    ...base,
    entries: [],
    collapsed: false,
  };
  return [...runs, update(created)];
}

function finalizeRunCard(
  runs: RunCardState[],
  runId: string,
  payload: AssistantResponse,
  anchorUserTurnIndex?: number,
): RunCardState[] {
  const trace = Array.isArray(payload.execution_trace) ? payload.execution_trace : [];
  const summary = buildRunSummary(payload);
  const error = payload.error || null;
  const existing = runs.find((run) => run.id === runId);

  if (!existing) {
    if (!trace.length && !error) {
      return runs;
    }
    return [
      ...runs,
        {
          id: runId,
          anchorUserTurnIndex: anchorUserTurnIndex ?? 0,
          approvalTokenId: payload.resume_token_id,
          capabilityName: payload.capability_name || "assistant",
          phaseLabel: summary,
          status: mapPayloadStatus(payload),
          entries: mergeFinalTraceEntries([], trace),
          collapsed: !shouldExpandRunCard(payload),
          summary,
          error,
        },
    ];
  }

  return runs.map((run) =>
    run.id !== runId
      ? run
        : {
          ...run,
          approvalTokenId: payload.resume_token_id,
          capabilityName: payload.capability_name || run.capabilityName,
          phaseLabel: summary,
          status: mapPayloadStatus(payload),
          entries: mergeFinalTraceEntries(run.entries, trace),
          collapsed: !shouldExpandRunCard(payload),
          summary,
          error,
        },
  );
}

function shouldExpandRunCard(payload: AssistantResponse): boolean {
  return Boolean(payload.error || payload.status === "error" || payload.resume_token_id);
}

function mapPayloadStatus(payload: AssistantResponse): RunCardState["status"] {
  if (payload.status === "error" || payload.error) {
    return "error";
  }
  if (payload.resume_token_id) {
    return "running";
  }
  return "completed";
}

function mergeFinalTraceEntries(entries: RunLogEntry[], trace: AssistantResponse["execution_trace"]): RunLogEntry[] {
  if (trace.length === 0) {
    return entries;
  }

  let nextEntries = [...entries];
  for (const step of trace) {
    nextEntries = appendRunEntry(nextEntries, step.status === "error" ? "error" : "step", formatStepLabel(step));
  }
  return nextEntries;
}

function inferCapabilityName(source: string | null | undefined, fallback: string): string {
  const text = String(source || "").trim();
  if (!text) {
    return fallback;
  }
  const firstSegment = text.split(/[·:|]/)[0]?.trim();
  if (!firstSegment) {
    return fallback;
  }
  if (firstSegment.length > 32) {
    return fallback;
  }
  return firstSegment;
}

function formatStepLabel(step: AssistantResponse["execution_trace"][number]): string {
  const detail = normalizeDetail(step.detail);
  return detail ? `${step.name} · ${step.status} · ${detail}` : `${step.name} · ${step.status}`;
}

function buildRunSummary(payload: AssistantResponse): string {
  const trace = Array.isArray(payload.execution_trace) ? payload.execution_trace : [];
  const lastTrace = trace[trace.length - 1];
  if (payload.status === "error" && payload.error) {
    return `${payload.error.code} · ${payload.error.message}`;
  }
  if (lastTrace?.detail) {
    return normalizeDetail(lastTrace.detail);
  }
  if (payload.capability_name) {
    return `已完成 ${payload.capability_name}`;
  }
  return "已完成";
}

function normalizeDetail(value: unknown): string {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (
    typeof value === "number" ||
    typeof value === "boolean" ||
    typeof value === "bigint"
  ) {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function extractAssistantError(error: unknown, fallbackMessage: string): AssistantError {
  if (error instanceof ApiRequestError && error.assistantError) {
    return error.assistantError;
  }
  const message = error instanceof Error ? error.message : fallbackMessage;
  return {
    code: "UI_REQUEST_ERROR",
    message: fallbackMessage,
    detail: message,
    stage: "ui",
    retriable: true,
    suggestion: "可以重试一次；如果持续失败，请检查后端返回的 trace_id 和日志。",
    trace_id: null,
    context: null,
  };
}

function compactText(value: string | null | undefined, limit = 120): string {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, limit)}... (${text.length} chars)`;
}

function maskApiKey(value: string): string {
  if (!value) {
    return "";
  }
  if (value.length <= 8) {
    return "*".repeat(value.length);
  }
  return `${value.slice(0, 5)}...${value.slice(-4)}`;
}

function RunCard({
  run,
  onToggle,
  setContainerRef,
  stageSummary,
  processDetails,
  onApproval,
  onReplay,
}: {
  run: RunCardState;
  onToggle: () => void;
  setContainerRef?: (element: HTMLDivElement | null) => void;
  stageSummary: RunStageSummary;
  processDetails: ProcessDetailState | null;
  onApproval: (approved: boolean) => void;
  onReplay?: () => void;
}) {
  const summaryText =
    normalizeDetail(run.collapsed ? run.summary : run.phaseLabel).trim() ||
    normalizeDetail(run.phaseLabel) ||
    normalizeDetail(run.summary) ||
    "运行中";
  const recentEntries = run.entries.slice(-2).reverse();
  const hasDetails = Boolean(
    run.error ||
      processDetails?.streamingReply ||
      processDetails?.pendingTokenId,
  );
  const subtleMeta = [
    stageSummary.total > 0 ? `${stageSummary.total} steps` : null,
    stageSummary.running ? "live" : null,
    stageSummary.error ? `${stageSummary.error} error` : null,
  ].filter(Boolean).join(" · ");

  return (
    <div ref={setContainerRef} className={`run-card ${run.status} ${run.collapsed ? "collapsed" : "expanded"}`}>
      <button type="button" className="run-card-header" onClick={onToggle}>
        <div className="run-card-header-main">
          <div className="run-header-topline">
            <span className={`run-status ${run.status}`}>{run.status}</span>
            <span className="run-header-label">执行 Agent</span>
          </div>
          <div className="run-header-copy">
            <strong>{run.capabilityName || "assistant"}</strong>
            <span className="run-summary">{summaryText}</span>
            {subtleMeta ? <span className="run-subtle-meta">{subtleMeta}</span> : null}
          </div>
        </div>
        <span className="run-toggle" aria-hidden="true">
          <span className={`run-toggle-chevron ${run.collapsed ? "collapsed" : "expanded"}`}>⌄</span>
          <span className="run-toggle-label">详情</span>
        </span>
      </button>
      {recentEntries.length > 0 ? (
        <div className="run-card-preview">
          {recentEntries.map((entry) => (
            <div key={entry.id} className={`run-preview-item ${entry.kind}`}>
              <span className="timeline-dot" />
              <span>{compactText(entry.text, 110)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="run-card-preview empty">
          <div className="run-preview-item status">
            <span className="timeline-dot" />
            <span>{run.status === "running" ? "等待事件流返回..." : "当前流程暂无步骤详情。"}</span>
          </div>
        </div>
      )}
      {!run.collapsed ? (
        <div className="run-card-body">
          <div className="run-detail-grid">
            <section className="run-detail-section">
              <div className="run-detail-head">
                <strong>Process</strong>
                <span className="section-meta">{run.entries.length} events</span>
              </div>
              <div className="agent-timeline compact-timeline">
                {run.entries.length === 0 ? (
                  <div className="compact-empty-state">
                    <strong>等待事件流</strong>
                    <p>{run.status === "running" ? "状态和步骤返回后会立即显示在这里。" : "这个流程没有附带更多执行细节。"}</p>
                  </div>
                ) : (
                  run.entries.map((entry, index) => (
                    <div key={entry.id} className={`agent-timeline-item ${entry.kind}`}>
                      <span className="timeline-dot" />
                      <div>
                        <div className="timeline-row">
                          <strong>{entry.kind === "status" ? "Status" : entry.kind === "step" ? `Step ${index + 1}` : entry.kind === "error" ? "Error" : "Notice"}</strong>
                          <span className={`mini-pill ${entry.kind === "status" ? "on" : ""} ${entry.kind === "error" ? "danger" : ""}`}>{entry.kind}</span>
                        </div>
                        <p>{entry.text}</p>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </section>

            {hasDetails ? (
              <section className="run-detail-section subtle-detail-panel">
                <div className="run-detail-head">
                  <strong>Actions</strong>
                  <span className="section-meta">optional</span>
                </div>

                {processDetails?.pendingTokenId ? (
                  <div className="approval approval-inline-card">
                    <div className="run-detail-head approval-inline-head">
                      <strong>Approval required</strong>
                      <span className="mini-pill">pending</span>
                    </div>
                    <p>{processDetails.approvalText}</p>
                    <div className="approval-actions">
                      <button type="button" className="primary" onClick={() => onApproval(true)}>
                        批准继续
                      </button>
                      <button type="button" className="ghost" onClick={() => onApproval(false)}>
                        拒绝
                      </button>
                    </div>
                  </div>
                ) : null}

                {processDetails?.streamingReply && processDetails.currentStatus === "streaming" ? (
                  <div className="workspace-section streaming-preview inline-streaming-preview compact-streaming-note">
                    <div className="workspace-section-head">
                      <span className="section-label">Answer</span>
                      <span className="typing-indicator">Generating</span>
                    </div>
                    <p>{compactText(processDetails.streamingReply, 140)}</p>
                  </div>
                ) : null}
              </section>
            ) : null}
          </div>

          {run.error ? (
            <div className="run-error-card">
              <strong>{run.error.message}</strong>
              {run.error.suggestion ? <p>{run.error.suggestion}</p> : null}
              <p className="error-meta">
                {run.error.code}
                {run.error.stage ? ` · ${run.error.stage}` : ""}
              </p>
              {run.error.retriable && onReplay ? (
                <button type="button" className="retry-btn" onClick={onReplay}>
                  重试
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function summarizeRunEntries(entries: RunLogEntry[], status: RunCardState["status"]): RunStageSummary {
  let total = 0;
  let error = 0;

  for (const entry of entries) {
    if (entry.kind === "step" || entry.kind === "error") {
      total += 1;
    }
    if (entry.kind === "error") {
      error += 1;
    }
  }

  const running = status === "running" && total > error ? 1 : 0;
  const completed = Math.max(total - error - running, 0);
  return { total, completed, running, error };
}

export default App;
