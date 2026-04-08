import type { ViewId } from "../../viewModels";

type MainHeaderProps = {
  activeView: ViewId;
  title: string;
};

export function MainHeader({ activeView, title }: MainHeaderProps) {
  return (
    <header className="main-header">
      <div className="main-header-copy">
        <span className="main-header-eyebrow">{activeView === "chat" ? "agent-runtime-framework" : "settings"}</span>
        <h1>{title}</h1>
      </div>
    </header>
  );
}
