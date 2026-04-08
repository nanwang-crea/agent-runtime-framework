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
  const recentEntries = run.entries.slice(-2).reverse();
  const subtleMeta = [
    stageSummary.total > 0 ? `${stageSummary.total} steps` : null,
    stageSummary.running ? "处理中" : null,
    stageSummary.error ? `${stageSummary.error} error` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const headline = run.collapsed ? run.summary : run.phaseLabel;

  return (
    <section ref={setContainerRef} className={`run-status-row ${run.status} ${run.collapsed ? "collapsed" : "expanded"}`}>
      <button type="button" className="run-status-header" onClick={onToggle}>
        <span className="run-status-line" />
        <span className="run-status-copy">
          <span className="run-inline-meta">
            <span className="run-inline-label">{run.capabilityName || "assistant"}</span>
            <span className={`run-badge ${run.status}`}>{run.status}</span>
          </span>
          <strong>{headline}</strong>
          {subtleMeta ? <small>{subtleMeta}</small> : null}
        </span>
        <span className="run-toggle">{run.collapsed ? "查看" : "隐藏"}</span>
        <span className="run-status-line" />
      </button>

      <div className="run-preview-strip">
        {recentEntries.length === 0 ? (
          <div className="run-preview-item">当前流程暂无额外步骤。</div>
        ) : (
          recentEntries.map((entry) => (
            <div key={entry.id} className={`run-preview-item ${entry.kind}`}>
              {entry.text}
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
              run.entries.map((entry, index) => (
                <div key={entry.id} className={`run-event-item ${entry.kind}`}>
                  <strong>{entry.kind === "step" ? `Step ${index + 1}` : entry.kind}</strong>
                  <p>{entry.text}</p>
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
