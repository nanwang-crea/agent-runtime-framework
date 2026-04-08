import type { SessionPayload } from "../../types";
import type { ThreadSummary, ViewId } from "../../viewModels";

type SidebarProps = {
  activeView: ViewId;
  workspace: string;
  status: string;
  session: SessionPayload;
  threads: ThreadSummary[];
  onNewChat: () => void;
  onSelectChat: () => void;
  onSelectSettings: () => void;
};

export function Sidebar({
  activeView,
  workspace,
  status,
  session,
  threads,
  onNewChat,
  onSelectChat,
  onSelectSettings,
}: SidebarProps) {
  return (
    <aside className="sidebar-shell">
      <div className="sidebar-top">
        <div className="sidebar-brand">
          <div className="brand-mark">AR</div>
          <div>
            <strong>Agent Runtime</strong>
            <p>workflow-first desktop shell</p>
          </div>
        </div>
        <button type="button" className="sidebar-new-chat" onClick={onNewChat}>
          新对话
        </button>
      </div>

      <div className="sidebar-section">
        <span className="sidebar-label">Workspace</span>
        <div className="sidebar-workspace-card">
          <strong>{workspace || "加载中..."}</strong>
          <span>{session.turns.length} 条消息</span>
        </div>
      </div>

      <div className="sidebar-section">
        <span className="sidebar-label">导航</span>
        <div className="sidebar-nav">
          <button
            type="button"
            className={`sidebar-nav-item ${activeView === "chat" ? "active" : ""}`}
            onClick={onSelectChat}
          >
            对话
          </button>
          <button
            type="button"
            className={`sidebar-nav-item ${activeView === "settings" ? "active" : ""}`}
            onClick={onSelectSettings}
          >
            设置
          </button>
        </div>
      </div>

      <div className="sidebar-section sidebar-threads">
        <div className="sidebar-section-head">
          <span className="sidebar-label">线程</span>
          <span className={`sidebar-status status-${status}`}>{status}</span>
        </div>
        <div className="thread-list">
          {threads.length === 0 ? (
            <div className="thread-empty">发送第一条消息后，这里会展示对话摘要。</div>
          ) : (
            threads.map((thread) => (
              <button
                key={thread.id}
                type="button"
                className={`thread-item ${thread.active ? "active" : ""}`}
                onClick={onSelectChat}
              >
                <strong>{thread.title}</strong>
                <span>{thread.subtitle}</span>
              </button>
            ))
          )}
        </div>
      </div>

      <div className="sidebar-footer">
        <button type="button" className="sidebar-settings-link" onClick={onSelectSettings}>
          设置
        </button>
      </div>
    </aside>
  );
}
