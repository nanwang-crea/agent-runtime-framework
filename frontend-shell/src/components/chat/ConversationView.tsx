import { Fragment, type FormEvent, type MutableRefObject, type RefObject } from "react";
import type { AssistantError } from "../../types";
import type { ChatItem, ProcessDetailState, RunCardState, RunStageSummary } from "../../viewModels";
import { Composer } from "./Composer";
import { MessageBubble } from "./MessageBubble";
import { RunStatusCard } from "./RunStatusCard";

type ConversationViewProps = {
  activeWorkspace: string;
  chatItems: ChatItem[];
  isBusy: boolean;
  message: string;
  status: string;
  uiError: AssistantError | null;
  showJumpToLatestRun: boolean;
  latestRunCardId: string | null;
  activeRun: RunCardState | null;
  messagesRef: RefObject<HTMLDivElement>;
  runCardRefs: MutableRefObject<Record<string, HTMLDivElement | null>>;
  onMessagesScroll: () => void;
  onJumpToLatestRun: () => void;
  onMessageChange: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  onStop: () => void;
  onApproval: (approved: boolean) => void;
  onReplay: (runId: string) => void;
  onToggleRun: (runId: string) => void;
  summarizeRunEntries: (entries: RunCardState["entries"], status: RunCardState["status"]) => RunStageSummary;
  runStageSummary: RunStageSummary;
  getProcessDetails: (runId: string) => ProcessDetailState | null;
};

export function ConversationView({
  activeWorkspace,
  chatItems,
  isBusy,
  message,
  status,
  uiError,
  showJumpToLatestRun,
  latestRunCardId,
  activeRun,
  messagesRef,
  runCardRefs,
  onMessagesScroll,
  onJumpToLatestRun,
  onMessageChange,
  onSubmit,
  onStop,
  onApproval,
  onReplay,
  onToggleRun,
  summarizeRunEntries,
  runStageSummary,
  getProcessDetails,
}: ConversationViewProps) {
  return (
    <section className="conversation-view">
      <div className="conversation-intro-card">
        <div>
          <span className="conversation-label">Agent Shell</span>
          <strong>{activeWorkspace || "当前工作区"}</strong>
          <p>消息流是主视图，流程与审批以内联轻卡片跟随，不再占用额外侧栏。</p>
        </div>
        <div className="conversation-intro-status">
          <span className="header-chip">{chatItems.length} items</span>
          <span className={`header-chip status-${status}`}>{status}</span>
        </div>
      </div>

      {uiError ? (
        <div className="ui-error-banner">
          <strong>{uiError.code} · {uiError.message}</strong>
          {uiError.suggestion ? <p>{uiError.suggestion}</p> : null}
        </div>
      ) : null}

      <div ref={messagesRef} className="message-stream" onScroll={onMessagesScroll}>
        {chatItems.length === 0 ? (
          <div className="stream-empty-state">
            <strong>开始一段对话</strong>
            <p>右侧只保留一个主对话窗口，发送消息后会在这里看到回答与执行过程。</p>
          </div>
        ) : (
          chatItems.map((item) => (
            <Fragment key={item.id}>
              {item.kind === "message" ? (
                <MessageBubble role={item.role} content={item.content} isStreaming={item.role === "assistant" && status === "streaming"} />
              ) : (
                <RunStatusCard
                  run={item.run}
                  setContainerRef={(element) => {
                    runCardRefs.current[item.run.id] = element;
                  }}
                  onToggle={() => onToggleRun(item.run.id)}
                  stageSummary={item.run.id === activeRun?.id ? runStageSummary : summarizeRunEntries(item.run.entries, item.run.status)}
                  processDetails={getProcessDetails(item.run.id)}
                  onApproval={onApproval}
                  onReplay={() => onReplay(item.run.id)}
                />
              )}
            </Fragment>
          ))
        )}
      </div>

      {showJumpToLatestRun && latestRunCardId ? (
        <div className="jump-row">
          <button type="button" className="secondary-button" onClick={onJumpToLatestRun}>
            跳到最新流程
          </button>
        </div>
      ) : null}

      <Composer message={message} disabled={isBusy} status={status} onChange={onMessageChange} onSubmit={onSubmit} onStop={onStop} />
    </section>
  );
}
