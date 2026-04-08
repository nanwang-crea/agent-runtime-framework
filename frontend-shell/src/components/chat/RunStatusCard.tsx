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
  const subtleMeta = [
    stageSummary.total > 0 ? `${stageSummary.total} 项过程` : null,
    stageSummary.running ? "处理中" : null,
    stageSummary.error ? `${stageSummary.error} 个异常` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const headline = run.collapsed ? run.summary : run.phaseLabel;

  return (
    <section ref={setContainerRef} className={`run-status-row ${run.status} ${run.collapsed ? "collapsed" : "expanded"}`}>
      {groupedSummary ? (
        <button type="button" className="run-section-toggle" onClick={onToggle}>
          <span className="run-section-line" />
          <span className="run-section-label">{groupedSummary}</span>
          <span className="run-section-caret">{run.collapsed ? "⌄" : "⌃"}</span>
          <span className="run-section-line" />
        </button>
      ) : null}

      <button type="button" className="run-status-header" onClick={onToggle}>
        <span className="run-status-line" />
        <span className="run-status-copy">
          <span className="run-inline-meta">
            <span className="run-inline-label">{run.capabilityName || "assistant"}</span>
            <span className={`run-badge ${run.status}`}>{statusLabel(run.status)}</span>
          </span>
          <strong>{headline}</strong>
          {subtleMeta ? <small>{subtleMeta}</small> : null}
        </span>
        <span className="run-toggle">{run.collapsed ? "查看" : "隐藏"}</span>
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
                    <strong>{entry.title}</strong>
                    {renderSecondary(entry) ? <p>{renderSecondary(entry)}</p> : null}
                  </div>
                </div>
              ))
            )}
          </div>

          {processDetails?.pendingTokenId ? (
            <div className="run-inline-panel approval-card">
              <strong>需要审批</strong>
              <p>{processDetails.approvalText}</p>
              <div className="approval-actions">
                <button type="button" className="primary-button" onClick={() => onApproval(true)}>
                  批准继续
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
    read: "已浏览",
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
  const subject = firstSubject(entry);
  switch (entry.kind) {
    case "read":
      return subject ? `Read ${subject}` : "Read";
    case "search":
      return subject ? `Searched ${subject}` : "Searched";
    case "edit":
      return subject ? `Edited ${subject}` : "Edited";
    case "exec":
      return subject ? `Ran ${subject}` : "Ran";
    case "test":
      return subject ? `Verified ${subject}` : "Verified";
    case "plan":
      return subject ? `Planned ${subject}` : "Planned";
    case "approval":
      return subject ? `Awaiting approval for ${subject}` : "Awaiting approval";
    case "error":
      return subject ? `Failed ${subject}` : "Failed";
    case "reply":
      return "Drafted reply";
    default:
      return entry.title;
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

function statusLabel(status: RunCardState["status"]): string {
  switch (status) {
    case "running":
      return "进行中";
    case "completed":
      return "已处理";
    case "error":
      return "异常";
    default:
      return status;
  }
}
