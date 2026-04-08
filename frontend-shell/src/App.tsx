import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { ConversationView } from "./components/chat/ConversationView";
import { MainHeader } from "./components/layout/MainHeader";
import { Sidebar } from "./components/layout/Sidebar";
import { SettingsView } from "./components/settings/SettingsView";
import type {
  AssistantError,
  AssistantResponse,
  ConfigResponse,
  ContextPayload,
  MemoryPayload,
  ModelCenterResponse,
  ModelsResponse,
  PlanPayload,
  SessionPayload,
} from "./types";
import type { RunCardState, RunLogEntry, RunStageSummary, ThreadSummary, ViewId } from "./viewModels";

const routedRoles = ["default", "conversation", "capability_selector", "planner", "interpreter", "resolver", "executor", "composer"];
const defaultWireApi = "chat_completions";

function App() {
  const [workspace, setWorkspace] = useState("");
  const [contextState, setContextState] = useState<ContextPayload>({
    active_workspace: "",
    available_workspaces: [],
  });
  const [session, setSession] = useState<SessionPayload>({ session_id: null, turns: [] });
  const [, setPlans] = useState<PlanPayload[]>([]);
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
  const [instanceDrafts, setInstanceDrafts] = useState<Record<string, { apiKey: string; baseUrl: string; wireApi: string }>>({});
  const [globalModelDraft, setGlobalModelDraft] = useState<{ instance: string; model: string }>({ instance: "", model: "" });
  const messagesRef = useRef<HTMLDivElement>(null);
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
          const wireApi = String((instanceCfg.connection || {})["wire_api"] || defaultWireApi);
          if (!next[instanceName]) {
            next[instanceName] = { apiKey: "", baseUrl, wireApi };
          } else if (!next[instanceName].baseUrl || !next[instanceName].wireApi) {
            next[instanceName] = {
              ...next[instanceName],
              baseUrl,
              wireApi,
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
    const instances = Object.entries(modelCenter.config.instances || {}).map(([instanceId, instanceCfg]) => ({
      instance: instanceId,
      type: instanceCfg.type,
      enabled: Boolean(instanceCfg.enabled),
      api_key_set: Boolean(instanceCfg.api_key_set),
      api_key_preview: String(instanceCfg.api_key_preview || ""),
      base_url: String((instanceCfg.connection || {})["base_url"] || ""),
      wire_api: String((instanceCfg.connection || {})["wire_api"] || defaultWireApi),
    }));
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
      setRunCards((current) => finalizeRunCard(current, runId, payload, anchorUserTurnIndex));
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
      const finalPayload = await sendMessageStream(
        trimmed,
        {
          onStart: () => setActiveView("chat"),
          onStatus: ({ label }) => {
            setRunCards((current) =>
              upsertRunCard(
                current,
                {
                  id: runId,
                  anchorUserTurnIndex,
                  capabilityName: "routing",
                  phaseLabel: label || "处理中",
                  status: "running",
                  summary: "运行中",
                  error: null,
                  approvalTokenId: null,
                },
                (run) => ({
                  ...run,
                  capabilityName: run.capabilityName === "routing" ? inferCapabilityName(label, run.capabilityName) : run.capabilityName,
                  phaseLabel: label || run.phaseLabel,
                  entries: appendRunEntry(run.entries, "status", label || "处理中"),
                }),
              ),
            );
          },
          onDelta: ({ delta }) => setStreamingReply((current) => current + delta),
          onStep: ({ step }) => {
            setRunCards((current) =>
              upsertRunCard(
                current,
                {
                  id: runId,
                  anchorUserTurnIndex,
                  capabilityName: "routing",
                  phaseLabel: "处理中",
                  status: "running",
                  summary: "运行中",
                  error: null,
                  approvalTokenId: null,
                },
                (run) => ({
                  ...run,
                  capabilityName: inferCapabilityName(step.name, run.capabilityName),
                  phaseLabel: normalizeDetail(step.detail) || step.name || run.phaseLabel,
                  entries: appendRunEntry(run.entries, step.status === "error" ? "error" : "step", formatStepLabel(step)),
                }),
              ),
            );
          },
          onMemory: ({ memory: nextMemory }) => setMemory(nextMemory),
          onError: ({ error }) => {
            setStatus("error");
            setRunCards((current) =>
              upsertRunCard(
                current,
                {
                  id: runId,
                  anchorUserTurnIndex,
                  capabilityName: "assistant",
                  phaseLabel: "请求失败",
                  status: "error",
                  summary: `${error.code} · ${error.message}`,
                  error,
                  approvalTokenId: null,
                },
                (run) => ({
                  ...run,
                  status: "error",
                  collapsed: false,
                  error,
                  summary: `${error.code} · ${error.message}`,
                  entries: appendRunEntry(run.entries, "error", `${error.code} · ${error.message}`),
                }),
              ),
            );
          },
          onFinal: (finalPayload) => applyResponse(finalPayload, runId, anchorUserTurnIndex),
        },
        abortController.signal,
      );
      abortControllerRef.current = null;
      if (finalPayload !== null) {
        setPendingUserMessage("");
      }
      setUiError(null);
    } catch (error) {
      abortControllerRef.current = null;
      if (error instanceof DOMException && error.name === "AbortError") {
        setRunCards((current) =>
          current.map((run) => (run.id === runId ? { ...run, status: "completed", phaseLabel: "已中止", summary: "用户已停止" } : run)),
        );
        return;
      }
      const message = error instanceof Error ? error.message : "流式请求失败";
      setStatus("error");
      setRunCards((current) =>
        upsertRunCard(
          current,
          {
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
          },
          (run) => ({
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
          }),
        ),
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

  function updateDraft(instanceId: string, key: "apiKey" | "baseUrl" | "wireApi", value: string) {
    setInstanceDrafts((current) => ({
      ...current,
      [instanceId]: {
        apiKey: current[instanceId]?.apiKey || "",
        baseUrl: current[instanceId]?.baseUrl || "",
        wireApi: current[instanceId]?.wireApi || defaultWireApi,
        [key]: value,
      },
    }));
  }

  async function handleAuth(instanceId: string) {
    try {
      const draft = instanceDrafts[instanceId] || { apiKey: "", baseUrl: "", wireApi: defaultWireApi };
      const updated = await updateModelCenter({
        instances: {
          [instanceId]: {
            credentials: { api_key: draft.apiKey },
            connection: { base_url: draft.baseUrl, wire_api: draft.wireApi },
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
      const routes = Object.fromEntries(routedRoles.map((role) => [role, { instance: instanceId, model: modelName }]));
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
    const routed = routedRoles.map((role) => models.routes[role]).find((route) => route?.instance === instanceId);
    return routed?.model_name || instanceState.models[0]?.model_name || "";
  }

  async function handleSaveConfig(instanceId: string) {
    try {
      const draft = instanceDrafts[instanceId] || { apiKey: "", baseUrl: "", wireApi: defaultWireApi };
      const payload = await updateModelCenter({
        instances: {
          [instanceId]: {
            credentials: { api_key: draft.apiKey },
            connection: { base_url: draft.baseUrl, wire_api: draft.wireApi },
          },
        },
      });
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

  useEffect(() => {
    setGlobalModelDraft((current) => {
      if (current.instance === selectedGlobalInstance && current.model === selectedGlobalModel) {
        return current;
      }
      return { instance: selectedGlobalInstance, model: selectedGlobalModel };
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
      (latestTurn.content === streamingReply || latestTurn.content.startsWith(streamingReply) || streamingReply.startsWith(latestTurn.content));
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
    const items: Array<{ id: string; kind: "message"; role: string; content: string } | { id: string; kind: "run"; run: RunCardState }> = [];
    let userIndex = 0;

    for (let index = 0; index < displayedTurns.length; index += 1) {
      const turn = displayedTurns[index];
      items.push({ id: `message-${index}-${turn.role}`, kind: "message", role: turn.role, content: turn.content });
      if (turn.role === "user") {
        const runsForTurn = runsByAnchor[userIndex] || [];
        for (const run of runsForTurn) {
          items.push({ id: `run-${run.id}`, kind: "run", run });
        }
        userIndex += 1;
      }
    }

    return items;
  }, [displayedTurns, runsByAnchor]);

  const activeWorkspace = contextState.active_workspace || workspace;
  const threads = useMemo<ThreadSummary[]>(() => {
    const userTurns = session.turns
      .map((turn, index) => ({ turn, index }))
      .filter(({ turn }) => turn.role === "user")
      .slice(-8)
      .reverse();
    return userTurns.map(({ turn, index }, listIndex) => ({
      id: `thread-${index}`,
      title: compactText(turn.content, 26) || `对话 ${index + 1}`,
      subtitle: `${session.turns.length - listIndex} 条上下文 · ${compactText(turn.content, 40)}`,
      active: activeView === "chat" && listIndex === 0,
    }));
  }, [activeView, session.turns]);

  const latestRunCardId = runCards.length > 0 ? runCards[runCards.length - 1].id : null;
  const latestRun = runCards.length > 0 ? runCards[runCards.length - 1] : null;
  const activeRun = [...runCards].reverse().find((run) => run.status === "running") || latestRun;

  const runStageSummary = useMemo<RunStageSummary>(() => {
    if (!activeRun) {
      return { total: 0, completed: 0, running: 0, error: 0 };
    }
    return summarizeRunEntries(activeRun.entries, activeRun.status);
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
    <main className="codex-shell">
      <Sidebar
        activeView={activeView}
        workspace={activeWorkspace}
        status={status}
        session={session}
        threads={threads}
        onNewChat={() => {
          setActiveView("chat");
          setMessage("");
          setPendingUserMessage("");
          setStreamingReply("");
          setRunCards([]);
          setPendingTokenId(null);
          setApprovalText("");
          setStatus("idle");
        }}
        onSelectChat={() => setActiveView("chat")}
        onSelectSettings={() => setActiveView("settings")}
      />

      <section className="main-shell">
        <MainHeader
          activeView={activeView}
          workspace={compactText(activeWorkspace || "workspace", 36)}
          status={status}
          title={activeView === "chat" ? "Agent Shell" : "设置"}
        />

        <div className="workspace-switch-row">
          <span className="conversation-label">切换工作区</span>
          <select value={contextState.active_workspace || workspace} onChange={(event) => void handleWorkspaceSwitch(event.target.value)}>
            {(contextState.available_workspaces.length > 0 ? contextState.available_workspaces : [workspace]).filter(Boolean).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </div>

        {activeView === "chat" ? (
          <ConversationView
            activeWorkspace={activeWorkspace}
            chatItems={chatItems}
            isBusy={isBusy}
            message={message}
            status={status}
            uiError={uiError}
            showJumpToLatestRun={showJumpToLatestRun}
            latestRunCardId={latestRunCardId}
            activeRun={activeRun || null}
            messagesRef={messagesRef}
            runCardRefs={runCardRefs}
            onMessagesScroll={handleMessagesScroll}
            onJumpToLatestRun={handleJumpToLatestRun}
            onMessageChange={setMessage}
            onSubmit={handleSubmit}
            onStop={handleStop}
            onApproval={(approved) => void handleApproval(approved)}
            onReplay={(runId) => void handleReplay(runId)}
            onToggleRun={(runId) =>
              setRunCards((current) => current.map((run) => (run.id === runId ? { ...run, collapsed: !run.collapsed } : run)))
            }
            summarizeRunEntries={summarizeRunEntries}
            runStageSummary={runStageSummary}
            getProcessDetails={(runId) =>
              runId === latestRunCardId
                ? {
                    streamingReply,
                    pendingTokenId,
                    approvalText,
                    currentStatus: status,
                  }
                : null
            }
          />
        ) : (
          <SettingsView
            config={config}
            models={models}
            globalModelDraft={globalModelDraft}
            instanceDrafts={instanceDrafts}
            defaultWireApi={defaultWireApi}
            onGlobalInstanceChange={(instance) => {
              const nextInstance = models.instances.find((item) => item.instance === instance);
              setGlobalModelDraft({
                instance,
                model: nextInstance?.models[0]?.model_name || "",
              });
            }}
            onGlobalModelChange={(model) => setGlobalModelDraft((current) => ({ ...current, model }))}
            onApplyGlobalModel={() => void handleDefaultModelSelect(globalModelDraft.instance, globalModelDraft.model)}
            onUpdateDraft={updateDraft}
            onSaveInstance={(instanceId) => void handleSaveConfig(instanceId)}
            onAuthInstance={(instanceId) => void handleAuth(instanceId)}
            onSetCurrentModel={(instanceId) => void handleDefaultModelSelect(instanceId, preferredModelForInstance(instanceId))}
          />
        )}
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
        capabilityName: inferCapabilityName(trace[trace.length - 1]?.name, "assistant"),
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
          capabilityName: trace.length ? inferCapabilityName(trace[trace.length - 1]?.name, run.capabilityName) : run.capabilityName,
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
  if (!firstSegment || firstSegment.length > 32) {
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
  if (lastTrace?.name) {
    return `已完成 ${lastTrace.name}`;
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
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
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
