import { FormEvent, useEffect, useMemo, useState } from "react";
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
import type {
  AssistantResponse,
  ConfigResponse,
  ExecutionTraceStep,
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

function App() {
  const [workspace, setWorkspace] = useState("");
  const [session, setSession] = useState<SessionPayload>({ session_id: null, turns: [] });
  const [plans, setPlans] = useState<PlanPayload[]>([]);
  const [models, setModels] = useState<ModelsResponse>({ providers: [], routes: {} });
  const [config, setConfig] = useState<ConfigResponse>({ path: "", providers: [], routes: {} });
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState("idle");
  const [pendingTokenId, setPendingTokenId] = useState<string | null>(null);
  const [approvalText, setApprovalText] = useState("");
  const [activeView, setActiveView] = useState<ViewId>("chat");
  const [streamingReply, setStreamingReply] = useState("");
  const [pendingUserMessage, setPendingUserMessage] = useState("");
  const [executionTrace, setExecutionTrace] = useState<ExecutionTraceStep[]>([]);
  const [providerDrafts, setProviderDrafts] = useState<Record<string, { apiKey: string; baseUrl: string }>>({});

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

  function applyResponse(payload: AssistantResponse) {
    setWorkspace(payload.workspace);
    setSession(payload.session);
    setPlans(payload.plan_history);
    setStatus(payload.status);
    setPendingTokenId(payload.resume_token_id);
    setExecutionTrace(payload.execution_trace || []);
    setApprovalText(
      payload.approval_request
        ? `${payload.approval_request.reason} | ${payload.approval_request.capability_name} | ${payload.approval_request.instruction}`
        : "",
    );
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed) {
      return;
    }
    setStatus("streaming");
    setPendingUserMessage(trimmed);
    setStreamingReply("");
    setExecutionTrace([]);
    setMessage("");
    await sendMessageStream(trimmed, {
      onStart: () => {
        setActiveView("chat");
      },
      onDelta: ({ delta }) => {
        setStreamingReply((current) => current + delta);
      },
      onStep: ({ step }) => {
        setExecutionTrace((current) => [...current, step]);
      },
      onFinal: (finalPayload) => {
        applyResponse(finalPayload);
      },
    });
    setPendingUserMessage("");
    setStreamingReply("");
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
    if (pendingUserMessage) {
      turns.push({ role: "user", content: pendingUserMessage });
    }
    if (streamingReply) {
      turns.push({ role: "assistant", content: streamingReply });
    }
    return turns;
  }, [pendingUserMessage, session.turns, streamingReply]);

  const latestPlan = plans.length > 0 ? plans[plans.length - 1] : null;

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

              <div className="messages">
                {displayedTurns.length === 0 ? (
                  <div className="empty-state">
                    <strong>开始一段对话</strong>
                    <p>你可以直接闲聊，也可以让我读取、总结、列出当前工作区的内容。</p>
                  </div>
                ) : (
                  displayedTurns.map((turn, index) => (
                    <div key={`${turn.role}-${index}`} className={`message ${turn.role} ${index === displayedTurns.length - 1 && streamingReply && turn.role === "assistant" ? "streaming" : ""}`}>
                      <small>{turn.role === "user" ? "You" : "Assistant"}</small>
                      <div>{turn.content}</div>
                    </div>
                  ))
                )}
              </div>

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
                <h3>Live Context</h3>
              </div>
              <div className="context-block">
                <span>Current Stream</span>
                <p>{streamingReply || "当前没有流式输出。"}</p>
              </div>
              <div className="context-block">
                <span>Execution Steps</span>
                {executionTrace.length > 0 ? (
                  <ul className="flat-list">
                    {executionTrace.map((step, index) => (
                      <li key={`${step.name}-${index}`}>
                        <strong>{step.name}</strong> · {step.status}
                        {step.detail ? ` · ${step.detail}` : ""}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>当前还没有执行步骤。</p>
                )}
              </div>
              <div className="context-block">
                <span>Latest Plan</span>
                {latestPlan ? (
                  <>
                    <strong>{latestPlan.goal}</strong>
                    <ul className="flat-list">
                      {latestPlan.steps.map((step, index) => (
                        <li key={`${latestPlan.plan_id}-${index}`}>
                          <strong>{step.capability_name}</strong> · {step.status}
                        </li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <p>还没有计划。</p>
                )}
              </div>
              <div className="context-block">
                <span>Model Route</span>
                <ul className="flat-list">
                  {modelRoles.map((role) => (
                    <li key={role}>
                      <strong>{role}</strong> · {models.routes[role]?.provider || "未设置"} / {models.routes[role]?.model_name || "未设置"}
                    </li>
                  ))}
                </ul>
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

export default App;
