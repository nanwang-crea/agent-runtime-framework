import { FormEvent, useEffect, useState } from "react";
import { authenticateProvider, fetchConfig, fetchModels, fetchSession, respondApproval, selectModel, sendMessage, updateConfig } from "./api";
import type { AssistantResponse, ConfigResponse, ModelsResponse, PlanPayload, SessionPayload } from "./types";

const examples = ["列出当前目录", "读取 README.md", "总结 README.md"];
const modelRoles = ["conversation", "capability_selector", "planner"];

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
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");

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
    const payload = await fetchModels();
    setModels(payload);
  }

  async function loadConfig() {
    const payload = await fetchConfig();
    setConfig(payload);
  }

  function applyResponse(payload: AssistantResponse) {
    setWorkspace(payload.workspace);
    setSession(payload.session);
    setPlans(payload.plan_history);
    setStatus(payload.status);
    setPendingTokenId(payload.resume_token_id);
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
    setStatus("running");
    setMessage("");
    applyResponse(await sendMessage(trimmed));
  }

  async function handleApproval(approved: boolean) {
    if (!pendingTokenId) {
      return;
    }
    setStatus("running");
    applyResponse(await respondApproval(pendingTokenId, approved));
  }

  async function handleAuth(provider: string) {
    const payload = await authenticateProvider(provider, apiKey, baseUrl);
    setModels(payload);
    await loadConfig();
    setApiKey("");
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
    const payload = await updateConfig({
      providers: {
        [provider]: {
          api_key: apiKey,
          base_url: baseUrl,
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
    setApiKey("");
  }

  return (
    <main className="shell">
      <section className="masthead">
        <div>
          <p className="kicker">Agent Runtime Framework</p>
          <h1>Desktop Assistant Shell</h1>
          <p className="lede">
            这是面向桌面 AI 助手的前端壳骨架。当前通过 demo API 驱动，后面会继续接到 Electron 原生能力层和更完整的 agent runtime。
          </p>
        </div>
        <div className="workspace">
          <span>Workspace</span>
          <code>{workspace || "加载中..."}</code>
        </div>
      </section>

      <section className="content-grid">
        <section className="card conversation">
          <div className="card-head">
            <h2>Conversation</h2>
            <span className={`pill ${status}`}>{status}</span>
          </div>

          <div className="examples">
            {examples.map((item) => (
              <button key={item} type="button" className="ghost" onClick={() => setMessage(item)}>
                {item}
              </button>
            ))}
          </div>

          <div className="messages">
            {session.turns.length === 0 ? (
              <div className="message assistant">发送第一条消息后，这里会显示 assistant 会话历史。</div>
            ) : (
              session.turns.map((turn, index) => (
                <div key={`${turn.role}-${index}`} className={`message ${turn.role}`}>
                  <small>{turn.role}</small>
                  <div>{turn.content}</div>
                </div>
              ))
            )}
          </div>

          <form className="composer" onSubmit={handleSubmit}>
            <textarea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="输入桌面任务，例如：总结 README.md"
            />
            <button type="submit" className="primary">发送</button>
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

        <section className="card">
          <div className="card-head">
            <h2>Plan Timeline</h2>
          </div>
          <div className="timeline">
            {plans.length === 0 ? (
              <div className="timeline-card">还没有计划历史。</div>
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

      <section className="card models-card">
        <div className="card-head">
          <h2>Model Center</h2>
        </div>
        <div className="config-summary">
          <span>Config Path</span>
          <code>{config.path || "加载中..."}</code>
        </div>
        <div className="models-grid">
          <div className="provider-panel">
            <h3>Config Center</h3>
            {config.providers.map((provider) => (
              <div key={`config-${provider.provider}`} className="provider-card">
                <div className="provider-head">
                  <strong>{provider.provider}</strong>
                  <span className={`pill ${provider.api_key_set ? "ready" : "idle"}`}>
                    {provider.api_key_set ? provider.api_key_preview : "not configured"}
                  </span>
                </div>
                <p className="provider-meta">默认 base URL: {provider.base_url || "未配置"}</p>
                <input
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder={`${provider.provider} API key`}
                />
                <input
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder={provider.base_url || "base URL"}
                />
                <button type="button" className="primary" onClick={() => void handleSaveConfig(provider.provider, "qwen3.5-plus")}>
                  保存为默认配置
                </button>
              </div>
            ))}

            <h3>Provider Auth</h3>
            {models.providers.map((provider) => (
              <div key={provider.provider} className="provider-card">
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
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder={`${provider.provider} API key`}
                />
                <input
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder="可选 base URL"
                />
                <button type="button" className="primary" onClick={() => void handleAuth(provider.provider)}>
                  登录 / 更新
                </button>
              </div>
            ))}
          </div>

          <div className="routes-panel">
            <h3>Role Routing</h3>
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
        </div>
      </section>
    </main>
  );
}

export default App;
