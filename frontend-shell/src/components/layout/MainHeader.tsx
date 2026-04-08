import type { ViewId } from "../../viewModels";

type MainHeaderProps = {
  activeView: ViewId;
  workspace: string;
  status: string;
  title: string;
};

export function MainHeader({ activeView, workspace, status, title }: MainHeaderProps) {
  return (
    <header className="main-header">
      <div>
        <span className="main-header-eyebrow">{activeView === "chat" ? "当前会话" : "模型设置"}</span>
        <h1>{title}</h1>
      </div>
      <div className="main-header-meta">
        <span className="header-chip">{workspace || "workspace"}</span>
        <span className={`header-chip status-${status}`}>{status}</span>
      </div>
    </header>
  );
}
