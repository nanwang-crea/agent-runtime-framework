import { FormEvent, Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  authenticateProvider,
  fetchConfig,
  fetchModels,
  fetchSession,
  respondApproval,
  selectModel,
  sendMessageStream,
  updateConfig,
} from "./api";
import { MarkdownContent } from "./components/MarkdownContent";
import type {
  AssistantError,
  AssistantResponse,
  ConfigResponse,
  MemoryPayload,
  ModelsResponse,
  PlanPayload,
  SessionPayload,
} from "./types";

const examples = ["你好", "列出当前目录", "读取 README.md", "总结 README.md"];
const modelRoles = ["conversation", "capability_selector", "planner"];
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

type RunCardState = {
  id: string;
  anchorUserTurnIndex: number;
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
  const [session, setSession] = useState<SessionPayload>({ session_id: null, turns: [] });
  const [plans, setPlans] = useState<PlanPayload[]>([]);
  const [memory, setMemory] = useState<MemoryPayload>({
    focused_resource: null,
    recent_resources: [],
    last_summary: null,
    active_capability: null,
  });
  const [models, setModels] = useState<ModelsResponse>({ providers: [], routes: {} });
  const [config, setConfig] = useState<ConfigResponse>({ path: "", providers: [], routes: {} });
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState("idle");
  const [streamStatus, setStreamStatus] = useState("等待新请求");
  const [runCards, setRunCards] = useState<RunCardState[]>([]);
  const [streamingReply, setStreamingReply] = useState("");
  const [pendingTokenId, setPendingTokenId] = useState<string | null>(null);
  const [approvalText, setApprovalText] = useState("");
  const [activeView, setActiveView] = useState<ViewId>("chat");
  const [pendingUserMessage, setPendingUserMessage] = useState("");
  const [showJumpToLatestRun, setShowJumpToLatestRun] = useState(false);
  const [providerDrafts, setProviderDrafts] = useState<Record<string, { apiKey: string; baseUrl: string }>>({});
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const runCardRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    void loadSession();
    void loadModels();
    void loadConfig();
  }, []);

  async function loadSession() {
    const payload = await fetchSession();
    setWorkspace(payload.workspace);
    setSession(payload.session);
    setPlans(payload.plan_history);
    setMemory(payload.memory);
  }

  async function loadModels() {
    setModels(await fetchModels());
  }

  async function loadConfig() {
    const payload = await fetchConfig();
    setConfig(payload);
    setProviderDrafts((current) => {
      const next = { ...current };
      for (const provider of payload.providers) {
        if (!next[provider.provider]) {
          next[provider.provider] = { apiKey: "", baseUrl: provider.base_url || "" };
        } else if (!next[provider.provider].baseUrl) {
          next[provider.provider] = {
            ...next[provider.provider],
            baseUrl: provider.base_url || "",
          };
        }
      }
      return next;
    });
  }

  function applyResponse(payload: AssistantResponse, runId?: string, anchorUserTurnIndex?: number) {
    setWorkspace(payload.workspace);
    setSession(payload.session);
    setPlans(payload.plan_history);
    setMemory(payload.memory);
    setStatus(payload.status);
    setPendingTokenId(payload.resume_token_id);
    setStreamStatus(payload.status === "error" ? "请求失败" : "请求完成");
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

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed) {
      return;
    }
    setStatus("streaming");
    setStreamStatus("请求已发送");
    const anchorUserTurnIndex = displayedTurns.filter((turn) => turn.role === "user").length;
    setPendingUserMessage(trimmed);
    setStreamingReply("");
    const runId = `run-${Date.now()}`;
    setMessage("");
    try {
      await sendMessageStream(trimmed, {
        onStart: () => {
          setActiveView("chat");
        },
        onStatus: ({ label }) => {
          setStreamStatus(label || "处理中");
          setRunCards((current) =>
            upsertRunCard(current, {
              id: runId,
              anchorUserTurnIndex,
              capabilityName: "routing",
              phaseLabel: label || "处理中",
              status: "running",
              summary: "运行中",
              error: null,
            }, (run) => ({
              ...run,
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
            }, (run) => ({
              ...run,
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
          setStreamStatus("请求失败");
          setRunCards((current) =>
            upsertRunCard(current, {
              id: runId,
              anchorUserTurnIndex,
              capabilityName: "assistant",
              phaseLabel: "请求失败",
              status: "error",
              summary: `${error.code} · ${error.message}`,
              error,
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
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "流式请求失败";
      setStatus("error");
      setStreamStatus("请求失败");
      setRunCards((current) =>
        upsertRunCard(current, {
          id: runId,
          anchorUserTurnIndex,
          capabilityName: "assistant",
          phaseLabel: "请求失败",
          status: "error",
          summary: message,
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
    setPendingUserMessage("");
  }

  async function handleApproval(approved: boolean) {
    if (!pendingTokenId) {
      return;
    }
    setStatus("running");
    applyResponse(await respondApproval(pendingTokenId, approved));
  }

  function updateDraft(provider: string, key: "apiKey" | "baseUrl", value: string) {
    setProviderDrafts((current) => ({
      ...current,
      [provider]: {
        apiKey: current[provider]?.apiKey || "",
        baseUrl: current[provider]?.baseUrl || "",
        [key]: value,
      },
    }));
  }

  async function handleAuth(provider: string) {
    const draft = providerDrafts[provider] || { apiKey: "", baseUrl: "" };
    const payload = await authenticateProvider(provider, draft.apiKey, draft.baseUrl);
    setModels(payload);
    await loadConfig();
    updateDraft(provider, "apiKey", "");
  }

  async function handleModelSelect(role: string, provider: string, modelName: string) {
    if (!provider || !modelName) {
      return;
    }
    const payload = await selectModel(role, provider, modelName);
    setModels(payload);
    await loadConfig();
  }

  async function handleSaveConfig(provider: string, modelName: string) {
    const draft = providerDrafts[provider] || { apiKey: "", baseUrl: "" };
    const payload = await updateConfig({
      providers: {
        [provider]: {
          api_key: draft.apiKey,
          base_url: draft.baseUrl,
        },
      },
      routes: {
        conversation: { provider, model_name: modelName },
        capability_selector: { provider, model_name: modelName },
        planner: { provider, model_name: modelName },
      },
    });
    setConfig(payload.config);
    setModels(payload.models);
    updateDraft(provider, "apiKey", "");
  }

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
        const runsForTurn = runCards.filter((run) => run.anchorUserTurnIndex === userIndex);
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
  }, [displayedTurns, runCards]);

  const latestRunCardId = runCards.length > 0 ? runCards[runCards.length - 1].id : null;

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
          <h1>Desktop Assistant</h1>
          <p className="brand-copy">聊天、桌面操作、模型设置和历史记录，现在分成一个真正可工作的桌面工作台。</p>
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
          <span>Workspace</span>
          <code>{workspace || "加载中..."}</code>
        </div>

        <div className="sidebar-stats">
          <div className="stat-card">
            <span>Session</span>
            <strong>{session.turns.length}</strong>
          </div>
          <div className="stat-card">
            <span>Plans</span>
            <strong>{plans.length}</strong>
          </div>
          <div className="stat-card">
            <span>Status</span>
            <strong>{status}</strong>
          </div>
        </div>
      </aside>

      <section className="main-stage">
        <header className="topbar">
          <div>
            <p className="eyebrow">{activeView === "chat" ? "Conversation Workspace" : activeView === "history" ? "Run History" : "Model & Config Center"}</p>
            <h2>{activeView === "chat" ? "Chat" : activeView === "history" ? "History" : "Settings"}</h2>
          </div>
          <span className={`pill ${status}`}>{status}</span>
        </header>

        {activeView === "chat" ? (
          <section className="chat-layout">
            <section className="panel conversation-panel">
              <div className="panel-head">
                <h3>Conversation</h3>
              </div>

              <div className="example-row">
                {examples.map((item) => (
                  <button key={item} type="button" className="ghost" onClick={() => setMessage(item)}>
                    {item}
                  </button>
                ))}
              </div>

              <div ref={messagesRef} className="messages" onScroll={handleMessagesScroll}>
                {chatItems.length === 0 ? (
                  <div className="empty-state">
                    <strong>开始一段对话</strong>
                    <p>你可以直接闲聊，也可以让我读取、总结、列出当前工作区的内容。</p>
                  </div>
                ) : (
                  chatItems.map((item) => (
                    <Fragment key={item.id}>
                      {item.kind === "message" ? (
                        <div className={`message ${item.role}`}>
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
                />
                <div className="composer-bar">
                  <span className="composer-hint">流式显示已开启，回复会边生成边落到聊天区。</span>
                  <button type="submit" className="primary">发送</button>
                </div>
              </form>

              {pendingTokenId ? (
                <div className="approval">
                  <p>{approvalText}</p>
                  <div className="approval-actions">
                    <button type="button" className="primary" onClick={() => void handleApproval(true)}>
                      批准继续
                    </button>
                    <button type="button" className="ghost" onClick={() => void handleApproval(false)}>
                      拒绝
                    </button>
                  </div>
                </div>
              ) : null}
            </section>

            <aside className="panel insight-panel">
              <div className="panel-head">
                <h3>Context</h3>
              </div>
              <div className="context-block">
                <span>Focused Resource</span>
                {memory.focused_resource ? (
                  <p>
                    {memory.focused_resource.title} · {memory.focused_resource.kind}
                  </p>
                ) : (
                  <p>当前没有焦点资源。</p>
                )}
              </div>
              <div className="context-block">
                <span>Working Summary</span>
                <p>{memory.last_summary || "当前还没有工作摘要。"}</p>
              </div>
            </aside>
          </section>
        ) : null}

        {activeView === "history" ? (
          <section className="history-layout">
            <section className="panel history-panel">
              <div className="panel-head">
                <h3>Chat History</h3>
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
                      <p>{turn.content}</p>
                    </div>
                  ))
                )}
              </div>
            </section>

            <section className="panel history-panel">
              <div className="panel-head">
                <h3>Plan Timeline</h3>
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
                      {plan.steps.map((step, index) => (
                        <div key={`${plan.plan_id}-${index}`} className="timeline-step">
                          <span className="step-status">{step.status}</span>
                          <strong>{step.capability_name}</strong>
                          <p>{step.instruction}</p>
                          {step.observation ? <code>{step.observation}</code> : null}
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
                <h3>Config Center</h3>
              </div>
              <div className="config-summary">
                <span>Config Path</span>
                <code>{config.path || "加载中..."}</code>
              </div>
              <div className="settings-grid">
                {config.providers.map((provider) => {
                  const draft = providerDrafts[provider.provider] || { apiKey: "", baseUrl: provider.base_url || "" };
                  return (
                    <div key={`config-${provider.provider}`} className="settings-card">
                      <div className="provider-head">
                        <strong>{provider.provider}</strong>
                        <span className={`pill ${provider.api_key_set ? "ready" : "idle"}`}>
                          {provider.api_key_set ? provider.api_key_preview : "not configured"}
                        </span>
                      </div>
                      <p className="provider-meta">默认 base URL: {provider.base_url || "未配置"}</p>
                      <input
                        value={draft.apiKey}
                        onChange={(event) => updateDraft(provider.provider, "apiKey", event.target.value)}
                        placeholder={`${provider.provider} API key`}
                      />
                      <input
                        value={draft.baseUrl}
                        onChange={(event) => updateDraft(provider.provider, "baseUrl", event.target.value)}
                        placeholder={provider.base_url || "base URL"}
                      />
                      <button type="button" className="primary" onClick={() => void handleSaveConfig(provider.provider, "qwen3.5-plus")}>
                        保存为默认配置
                      </button>
                    </div>
                  );
                })}
              </div>
            </section>

            <section className="panel settings-panel">
              <div className="panel-head">
                <h3>Provider Auth & Model Routing</h3>
              </div>
              <div className="settings-grid">
                {models.providers.map((provider) => {
                  const draft = providerDrafts[provider.provider] || { apiKey: "", baseUrl: "" };
                  return (
                    <div key={provider.provider} className="settings-card">
                      <div className="provider-head">
                        <strong>{provider.provider}</strong>
                        <span className={`pill ${provider.authenticated ? "ready" : "idle"}`}>
                          {provider.authenticated ? "authenticated" : "not ready"}
                        </span>
                      </div>
                      <p className="provider-meta">
                        {provider.auth_session?.error_message || "通过 API key 完成 provider 登录。"}
                      </p>
                      <input
                        value={draft.apiKey}
                        onChange={(event) => updateDraft(provider.provider, "apiKey", event.target.value)}
                        placeholder={`${provider.provider} API key`}
                      />
                      <input
                        value={draft.baseUrl}
                        onChange={(event) => updateDraft(provider.provider, "baseUrl", event.target.value)}
                        placeholder="可选 base URL"
                      />
                      <button type="button" className="primary" onClick={() => void handleAuth(provider.provider)}>
                        登录 / 更新
                      </button>
                    </div>
                  );
                })}
              </div>

              <div className="route-grid">
                {modelRoles.map((role) => {
                  const selected = models.routes[role];
                  const provider = models.providers.find((item) => item.provider === selected?.provider) || models.providers[0];
                  return (
                    <div key={role} className="route-card">
                      <label>{role}</label>
                      <select
                        value={provider?.provider || ""}
                        onChange={(event) => {
                          const nextProvider = models.providers.find((item) => item.provider === event.target.value);
                          const nextModel = nextProvider?.models[0]?.model_name || "";
                          void handleModelSelect(role, event.target.value, nextModel);
                        }}
                      >
                        <option value="">选择 provider</option>
                        {models.providers.map((item) => (
                          <option key={item.provider} value={item.provider}>
                            {item.provider}
                          </option>
                        ))}
                      </select>
                      <select
                        value={selected?.model_name || provider?.models[0]?.model_name || ""}
                        onChange={(event) => void handleModelSelect(role, provider?.provider || "", event.target.value)}
                      >
                        <option value="">选择模型</option>
                        {(provider?.models || []).map((model) => (
                          <option key={`${provider?.provider}-${model.model_name}`} value={model.model_name}>
                            {model.display_name}
                          </option>
                        ))}
                      </select>
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
  const summary = buildRunSummary(payload);
  const error = payload.error || null;
  const existing = runs.find((run) => run.id === runId);

  if (!existing) {
    if (!payload.execution_trace.length && !error) {
      return runs;
    }
    return [
      ...runs,
      {
        id: runId,
        anchorUserTurnIndex: anchorUserTurnIndex ?? 0,
        capabilityName: payload.capability_name || "assistant",
        phaseLabel: summary,
        status: payload.status === "error" ? "error" : "completed",
        entries: payload.execution_trace.map((step, index) => ({
          id: `final-step-${index}-${step.name}`,
          kind: step.status === "error" ? "error" : "step",
          text: formatStepLabel(step),
        })),
        collapsed: payload.status !== "error",
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
          capabilityName: payload.capability_name || run.capabilityName,
          phaseLabel: summary,
          status: payload.status === "error" ? "error" : "completed",
          collapsed: payload.status !== "error",
          summary,
          error,
        },
  );
}

function formatStepLabel(step: AssistantResponse["execution_trace"][number]): string {
  return step.detail ? `${step.name} · ${step.status} · ${step.detail}` : `${step.name} · ${step.status}`;
}

function buildRunSummary(payload: AssistantResponse): string {
  const lastTrace = payload.execution_trace[payload.execution_trace.length - 1];
  if (payload.status === "error" && payload.error) {
    return `${payload.error.code} · ${payload.error.message}`;
  }
  if (lastTrace?.detail) {
    return lastTrace.detail;
  }
  if (payload.capability_name) {
    return `已完成 ${payload.capability_name}`;
  }
  return "已完成";
}

function RunCard({
  run,
  onToggle,
  setContainerRef,
}: {
  run: RunCardState;
  onToggle: () => void;
  setContainerRef?: (element: HTMLDivElement | null) => void;
}) {
  const summaryText = (run.collapsed ? run.summary : run.phaseLabel).trim() || run.phaseLabel || run.summary || "运行中";

  return (
    <div ref={setContainerRef} className={`run-card ${run.status} ${run.collapsed ? "collapsed" : "expanded"}`}>
      <button type="button" className="run-card-header" onClick={onToggle}>
        <span className={`run-status ${run.status}`}>{run.status}</span>
        <div className="run-header-copy">
          <strong>{run.capabilityName || "assistant"}</strong>
          <span className="run-summary">{summaryText}</span>
        </div>
        <span className="run-toggle">{run.collapsed ? "展开" : "收起"}</span>
      </button>
      {!run.collapsed ? (
        <div className="run-card-body">
          <ul className="run-log">
            {run.entries.map((entry) => (
              <li key={entry.id} className={`run-log-entry ${entry.kind}`}>
                {entry.text}
              </li>
            ))}
          </ul>
          {run.error ? (
            <div className="run-error-card">
              <strong>{run.error.message}</strong>
              {run.error.suggestion ? <p>{run.error.suggestion}</p> : null}
              <p className="error-meta">
                {run.error.code}
                {run.error.stage ? ` · ${run.error.stage}` : ""}
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default App;
