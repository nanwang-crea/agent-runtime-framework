import { FormEvent, useEffect, useState } from "react";
import { fetchSession, respondApproval, sendMessage } from "./api";
import type { AssistantResponse, PlanPayload, SessionPayload } from "./types";

const examples = ["列出当前目录", "读取 README.md", "总结 README.md"];

function App() {
  const [workspace, setWorkspace] = useState("");
  const [session, setSession] = useState<SessionPayload>({ session_id: null, turns: [] });
  const [plans, setPlans] = useState<PlanPayload[]>([]);
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState("idle");
  const [pendingTokenId, setPendingTokenId] = useState<string | null>(null);
  const [approvalText, setApprovalText] = useState("");

  useEffect(() => {
    void loadSession();
  }, []);

  async function loadSession() {
    const payload = await fetchSession();
    setWorkspace(payload.workspace);
    setSession(payload.session);
    setPlans(payload.plan_history);
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
    </main>
  );
}

export default App;
