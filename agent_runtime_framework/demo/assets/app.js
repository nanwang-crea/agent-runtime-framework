const state = {
  pendingTokenId: null,
};

const elements = {
  workspacePath: document.getElementById("workspacePath"),
  messages: document.getElementById("messages"),
  planHistory: document.getElementById("planHistory"),
  statusBadge: document.getElementById("statusBadge"),
  chatForm: document.getElementById("chatForm"),
  messageInput: document.getElementById("messageInput"),
  approvalBox: document.getElementById("approvalBox"),
  approvalText: document.getElementById("approvalText"),
  approveBtn: document.getElementById("approveBtn"),
  rejectBtn: document.getElementById("rejectBtn"),
};

async function fetchSession() {
  const response = await fetch("/api/session");
  const payload = await response.json();
  renderSession(payload);
}

async function postJson(url, data) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return response.json();
}

function setStatus(status) {
  elements.statusBadge.textContent = status;
  elements.statusBadge.className = `badge ${status === "needs_approval" ? "approval" : status === "completed" ? "idle" : "running"}`;
}

function renderSession(payload) {
  if (payload.workspace) {
    elements.workspacePath.textContent = payload.workspace;
  }
  renderMessages(payload.session?.turns || []);
  renderPlans(payload.plan_history || []);
}

function renderMessages(turns) {
  elements.messages.innerHTML = "";
  if (!turns.length) {
    const empty = document.createElement("div");
    empty.className = "message assistant";
    empty.textContent = "还没有对话，先试试左侧的示例请求。";
    elements.messages.appendChild(empty);
    return;
  }
  for (const turn of turns) {
    const item = document.createElement("div");
    item.className = `message ${turn.role}`;
    item.innerHTML = `<small>${turn.role}</small>${escapeHtml(turn.content)}`;
    elements.messages.appendChild(item);
  }
  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function renderPlans(plans) {
  elements.planHistory.innerHTML = "";
  if (!plans.length) {
    elements.planHistory.innerHTML = `<div class="plan-card"><h3>暂无计划历史</h3><p>发送消息后，这里会展示 assistant loop 形成的步骤。</p></div>`;
    return;
  }
  for (const plan of [...plans].reverse()) {
    const card = document.createElement("div");
    card.className = "plan-card";
    const steps = plan.steps.map((step) => `
      <div class="step">
        <span class="step-status">${escapeHtml(step.status)}</span>
        <div><strong>${escapeHtml(step.capability_name)}</strong></div>
        <div>${escapeHtml(step.instruction)}</div>
        ${step.observation ? `<div><code>${escapeHtml(step.observation)}</code></div>` : ""}
      </div>
    `).join("");
    card.innerHTML = `<h3>${escapeHtml(plan.goal)}</h3>${steps}`;
    elements.planHistory.appendChild(card);
  }
}

function showApproval(request, tokenId) {
  if (!request || !tokenId) {
    state.pendingTokenId = null;
    elements.approvalBox.classList.add("hidden");
    elements.approvalText.textContent = "";
    return;
  }
  state.pendingTokenId = tokenId;
  elements.approvalText.textContent = `${request.reason} | capability: ${request.capability_name} | instruction: ${request.instruction}`;
  elements.approvalBox.classList.remove("hidden");
}

async function sendMessage(message) {
  setStatus("running");
  const payload = await postJson("/api/chat", { message });
  setStatus(payload.status || "idle");
  renderSession(payload);
  showApproval(payload.approval_request, payload.resume_token_id);
}

async function respondApproval(approved) {
  if (!state.pendingTokenId) {
    return;
  }
  setStatus("running");
  const payload = await postJson("/api/approve", {
    token_id: state.pendingTokenId,
    approved,
  });
  setStatus(payload.status || "idle");
  renderSession(payload);
  showApproval(payload.approval_request, payload.resume_token_id);
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;")
    .replaceAll("\n", "<br />");
}

elements.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = elements.messageInput.value.trim();
  if (!message) {
    return;
  }
  elements.messageInput.value = "";
  await sendMessage(message);
});

elements.approveBtn.addEventListener("click", async () => {
  await respondApproval(true);
});

elements.rejectBtn.addEventListener("click", async () => {
  await respondApproval(false);
});

document.querySelectorAll(".example").forEach((button) => {
  button.addEventListener("click", async () => {
    const message = button.dataset.message;
    if (message) {
      await sendMessage(message);
    }
  });
});

fetchSession().catch((error) => {
  console.error(error);
  setStatus("idle");
});
