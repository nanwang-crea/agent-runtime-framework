import type { ProcessDetailState, RunCardState, RunStageSummary } from "../../viewModels";

type RunStatusCardProps = {
  run: RunCardState;
  stageSummary: RunStageSummary;
  processDetails: ProcessDetailState | null;
  setContainerRef?: (element: HTMLDivElement | null) => void;
  onToggle: () => void;
  onApproval: (approved: boolean) => void;
  onReplay?: () => void;
};

export function RunStatusCard({
  run,
  stageSummary,
  processDetails,
  setContainerRef,
  onToggle,
  onApproval,
  onReplay,
}: RunStatusCardProps) {
  const recentEntries = run.entries.slice(-3).reverse();
  const groupedSummary = summarizeEntries(run.entries);
  const subtleMeta = summarizeProgress(stageSummary, run.status);
  const headline = displayHeadline(run);
  const statusTone = statusToneLabel(run.status);

  return (
    <section ref={setContainerRef} className={`run-status-row ${run.status} ${run.collapsed ? "collapsed" : "expanded"}`}>
      {groupedSummary ? (
        <button type="button" className="run-section-toggle" onClick={onToggle}>
          <span className="run-section-line" />
          <span className="run-section-label">{groupedSummary}</span>
          <span className="run-section-caret" aria-hidden="true">{run.collapsed ? "⌄" : "⌃"}</span>
          <span className="run-section-line" />
        </button>
      ) : null}

      <button type="button" className="run-status-header" onClick={onToggle}>
        <span className="run-status-line" />
        <span className="run-status-copy">
          <span className="run-inline-meta">
            <span className="run-inline-label">{capabilityLabel(run.capabilityName)}</span>
            <span className={`run-badge ${run.status}`}>{statusTone}</span>
          </span>
          <strong>{headline}</strong>
          {subtleMeta ? <small>{subtleMeta}</small> : null}
        </span>
        <span className="run-toggle" aria-hidden="true">{run.collapsed ? "⌄" : "⌃"}</span>
        <span className="run-status-line" />
      </button>

      <div className="run-preview-strip">
        {recentEntries.length === 0 ? (
          <div className="run-preview-item">当前流程暂无额外过程。</div>
        ) : (
          recentEntries.map((entry) => (
            <div key={entry.id} className={`run-preview-item ${entry.kind} ${entry.metadata.repair ? "repair" : ""}`}>
              <span className="run-preview-icon">{iconForEntry(entry)}</span>
              <span className="run-preview-copy">
                <strong>{previewLabel(entry)}</strong>
                {previewDetail(entry) ? <small>{previewDetail(entry)}</small> : null}
              </span>
            </div>
          ))
        )}
      </div>

      {!run.collapsed ? (
        <div className="run-status-details">
          <div className="run-event-list">
            {run.entries.length === 0 ? (
              <div className="run-empty-state">等待事件流返回更多执行细节。</div>
            ) : (
              run.entries.map((entry) => (
                <div key={entry.id} className={`run-event-item ${entry.kind} ${entry.metadata.repair ? "repair" : ""}`}>
                  <span className="run-event-icon">{iconForEntry(entry)}</span>
                  <div className="run-event-copy">
                    <strong>{eventTitle(entry)}</strong>
                    {renderSecondary(entry) ? <p>{renderSecondary(entry)}</p> : null}
                  </div>
                </div>
              ))
            )}
          </div>

          {processDetails?.pendingTokenId ? (
            <div className="run-inline-panel approval-card">
              <strong>需要确认</strong>
              <p>{processDetails.approvalText}</p>
              <div className="approval-actions">
                <button type="button" className="primary-button" onClick={() => onApproval(true)}>
                  继续
                </button>
                <button type="button" className="secondary-button" onClick={() => onApproval(false)}>
                  拒绝
                </button>
              </div>
            </div>
          ) : null}

          {processDetails?.streamingReply && processDetails.currentStatus === "streaming" ? (
            <div className="run-inline-panel run-streaming-note">
              <strong>正在生成回答</strong>
              <p>{processDetails.streamingReply}</p>
            </div>
          ) : null}

          {run.error ? (
            <div className="run-inline-panel run-error-box">
              <strong>{run.error.message}</strong>
              {run.error.suggestion ? <p>{run.error.suggestion}</p> : null}
              {run.error.retriable && onReplay ? (
                <button type="button" className="secondary-button" onClick={onReplay}>
                  重试
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function summarizeEntries(entries: RunCardState["entries"]): string {
  const counts = new Map<string, number>();
  for (const entry of entries) {
    if (entry.kind === "status" || entry.kind === "reply") continue;
    counts.set(entry.kind, (counts.get(entry.kind) || 0) + 1);
  }
  const labels: Record<string, string> = {
    read: "已查看",
    search: "已搜索",
    edit: "已修改",
    exec: "已执行",
    test: "已验证",
    plan: "已规划",
    approval: "待确认",
    error: "异常",
  };
  return [...counts.entries()]
    .slice(0, 3)
    .map(([kind, count]) => `${labels[kind] || kind} ${count} ${kind === "approval" || kind === "error" ? "项" : "次"}`)
    .join("，");
}

function summarizeProgress(stageSummary: RunStageSummary, status: RunCardState["status"]): string {
  const parts = [];
  if (stageSummary.total > 0) {
    parts.push(`已完成 ${stageSummary.completed}/${stageSummary.total}`);
  }
  if (stageSummary.running > 0) {
    parts.push(`当前 ${stageSummary.running} 项进行中`);
  } else if (status === "completed") {
    parts.push("本轮处理完成");
  }
  if (stageSummary.error > 0) {
    parts.push(`${stageSummary.error} 项异常`);
  }
  return parts.join(" · ") || "正在准备处理步骤";
}

function displayHeadline(run: RunCardState): string {
  if (run.phaseLabel && run.phaseLabel !== run.summary) {
    return normalizeText(run.phaseLabel);
  }
  return normalizeText(run.summary || "正在处理");
}

function renderSecondary(entry: RunCardState["entries"][number]): string {
  const files = Array.isArray(entry.metadata.files) ? entry.metadata.files.map(String).filter(Boolean) : [];
  const changed = Array.isArray(entry.metadata.changed_paths) ? entry.metadata.changed_paths.map(String).filter(Boolean) : [];
  if (changed.length) {
    return changed.slice(0, 3).join("  ");
  }
  if (files.length) {
    return files.slice(0, 3).join("  ");
  }
  if (typeof entry.metadata.query === "string" && entry.metadata.query.trim()) {
    return entry.metadata.query.trim();
  }
  if (typeof entry.metadata.command === "string" && entry.metadata.command.trim()) {
    return entry.metadata.command.trim();
  }
  return entry.target || entry.detail;
}

function previewLabel(entry: RunCardState["entries"][number]): string {
  const subject = shorten(firstSubject(entry), 44);
  switch (entry.kind) {
    case "read":
      return subject ? `查看 ${subject}` : "查看内容";
    case "search":
      return subject ? `搜索 ${subject}` : "搜索线索";
    case "edit":
      return subject ? `修改 ${subject}` : "修改内容";
    case "exec":
      return subject ? `执行 ${subject}` : "执行命令";
    case "test":
      return subject ? `验证 ${subject}` : "执行验证";
    case "plan":
      return subject ? `规划 ${subject}` : "规划下一步";
    case "approval":
      return subject ? `等待确认 ${subject}` : "等待确认";
    case "error":
      return subject ? `${subject} 出现问题` : "处理遇到问题";
    case "reply":
      return "整理答复";
    default:
      return normalizeText(entry.title);
  }
}

function previewDetail(entry: RunCardState["entries"][number]): string {
  const secondary = renderSecondary(entry);
  if (secondary && secondary !== firstSubject(entry)) {
    return secondary;
  }
  return entry.detail;
}

function firstSubject(entry: RunCardState["entries"][number]): string {
  const changed = Array.isArray(entry.metadata.changed_paths) ? entry.metadata.changed_paths.map(String).filter(Boolean) : [];
  if (changed.length) {
    return changed[0];
  }
  const files = Array.isArray(entry.metadata.files) ? entry.metadata.files.map(String).filter(Boolean) : [];
  if (files.length) {
    return files[0];
  }
  if (typeof entry.metadata.query === "string" && entry.metadata.query.trim()) {
    return entry.metadata.query.trim();
  }
  if (typeof entry.metadata.command === "string" && entry.metadata.command.trim()) {
    return entry.metadata.command.trim();
  }
  return entry.target || "";
}

function eventTitle(entry: RunCardState["entries"][number]): string {
  if (entry.kind === "reply") {
    return "生成最终答复";
  }
  if (entry.kind === "plan") {
    return entry.title === "规划下一步" ? "规划下一步" : normalizeText(entry.title);
  }
  if (entry.kind === "approval") {
    return "等待人工确认";
  }
  if (entry.kind === "error") {
    return normalizeText(entry.title || "处理失败");
  }
  return normalizeText(entry.title || previewLabel(entry));
}

function iconForEntry(entry: RunCardState["entries"][number]): string {
  if (entry.metadata.repair) {
    return "↺";
  }
  return iconForKind(entry.kind);
}

function iconForKind(kind: RunCardState["entries"][number]["kind"]): string {
  switch (kind) {
    case "plan":
      return "○";
    case "read":
      return "◦";
    case "search":
      return "⌕";
    case "exec":
      return "›";
    case "edit":
      return "·";
    case "test":
      return "✓";
    case "reply":
      return "◦";
    case "approval":
      return "!";
    case "error":
      return "×";
    default:
      return "·";
  }
}

function capabilityLabel(value: string): string {
  const normalized = String(value || "").trim();
  if (!normalized) {
    return "assistant";
  }
  if (normalized.toLowerCase() === "final_response") {
    return "reply";
  }
  return normalized.toLowerCase();
}

function statusToneLabel(status: RunCardState["status"]): string {
  switch (status) {
    case "running":
      return "进行中";
    case "completed":
      return "已完成";
    case "error":
      return "异常";
    default:
      return status;
  }
}

function normalizeText(value: string): string {
  return value
    .replace(/^Planned\s+/i, "已规划 ")
    .replace(/^Read\s+/i, "查看 ")
    .replace(/^Searched\s+/i, "搜索 ")
    .replace(/^Edited\s+/i, "修改 ")
    .replace(/^Ran\s+/i, "执行 ")
    .replace(/^Verified\s+/i, "验证 ")
    .replace(/^Drafted reply$/i, "生成回答")
    .replace(/^FINAL_RESPONSE$/i, "生成最终答复")
    .trim();
}

function shorten(value: string, limit: number): string {
  if (value.length <= limit) {
    return value;
  }
  return `${value.slice(0, limit - 1)}…`;
}
