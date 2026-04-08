import type { SessionPayload } from "../../types";
import type { ThreadSummary, ViewId } from "../../viewModels";

type SidebarProps = {
  activeView: ViewId;
  workspace: string;
  availableWorkspaces: string[];
  session: SessionPayload;
  threads: ThreadSummary[];
  onNewChat: () => void;
  onSelectChat: () => void;
  onSelectSettings: () => void;
  onSelectWorkspace: (workspace: string) => void;
};

export function Sidebar({
  activeView,
  workspace,
  availableWorkspaces,
  session,
  threads,
  onNewChat,
  onSelectChat,
  onSelectSettings,
  onSelectWorkspace,
}: SidebarProps) {
  return (
    <aside className="sidebar-shell">
      <div className="sidebar-topbar">
        <button type="button" className="sidebar-icon-button" onClick={onNewChat}>
          新线程
        </button>
        <button type="button" className="sidebar-icon-button sidebar-placeholder" disabled title="后续接入技能面板">
          技能
        </button>
      </div>

      <div className="sidebar-nav">
        <button type="button" className={`sidebar-nav-item ${activeView === "chat" ? "active" : ""}`} onClick={onSelectChat}>
          对话
        </button>
        <button type="button" className={`sidebar-nav-item ${activeView === "settings" ? "active" : ""}`} onClick={onSelectSettings}>
          设置
        </button>
      </div>

      <div className="sidebar-thread-header">
        <span className="sidebar-label">线程</span>
      </div>

      <div className="thread-list">
        <button type="button" className="thread-quick-action" onClick={onNewChat}>
          + 新建会话
        </button>

        <div className="thread-list-body">
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
        <div className="sidebar-footer-meta">
          <span>{session.turns.length} 条上下文</span>
          <span>{workspace ? "本地工作区" : "未加载"}</span>
        </div>
        <div className="sidebar-footer-workspace">
          <span className="sidebar-label">工作区</span>
          <select value={workspace} onChange={(event) => onSelectWorkspace(event.target.value)}>
            {availableWorkspaces.filter(Boolean).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </div>
      </div>
    </aside>
  );
}
