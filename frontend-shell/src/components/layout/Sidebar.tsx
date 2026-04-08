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

function workspaceLabel(workspace: string): string {
  const normalized = String(workspace || "").trim();
  if (!normalized) {
    return "当前工作区";
  }
  const parts = normalized.split("/").filter(Boolean);
  return parts[parts.length - 1] || normalized;
}

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
      <div className="sidebar-menu">
        <button type="button" className="sidebar-menu-item" onClick={onNewChat}>
          <span className="sidebar-menu-icon">⌐</span>
          <span>新线程</span>
        </button>
        <button type="button" className="sidebar-menu-item sidebar-menu-placeholder" disabled>
          <span className="sidebar-menu-icon">◫</span>
          <span>技能</span>
        </button>
        <button type="button" className="sidebar-menu-item sidebar-menu-placeholder" disabled>
          <span className="sidebar-menu-icon">◎</span>
          <span>Plugins</span>
        </button>
        <button type="button" className="sidebar-menu-item sidebar-menu-active" disabled>
          <span className="sidebar-menu-icon">◷</span>
          <span>自动化</span>
        </button>
      </div>

      <div className="sidebar-thread-panel">
        <div className="sidebar-thread-header">
          <span className="sidebar-label">线程</span>
          <div className="sidebar-thread-tools">
            <button type="button" className="sidebar-tool-button" title="放大" disabled>
              ↗
            </button>
            <button type="button" className="sidebar-tool-button" title="筛选" disabled>
              ≡
            </button>
            <button type="button" className="sidebar-tool-button" title="新建" onClick={onNewChat}>
              ＋
            </button>
          </div>
        </div>

        <div className="thread-list">
          <div className="thread-group">
            <button type="button" className="thread-group-title active" onClick={onSelectChat}>
              <span className="thread-group-icon">▣</span>
              <span>{workspaceLabel(workspace)}</span>
            </button>
            <div className="thread-list-body">
              {threads.length === 0 ? (
                <div className="thread-empty">无线程</div>
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
        <button
          type="button"
          className={`sidebar-settings-row ${activeView === "settings" ? "active" : ""}`}
          onClick={onSelectSettings}
        >
          <span className="sidebar-menu-icon">◌</span>
          <span>设置</span>
        </button>
      </div>
    </aside>
  );
}
