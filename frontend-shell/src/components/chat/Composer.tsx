import type { FormEvent } from "react";

type ComposerProps = {
  message: string;
  disabled: boolean;
  status: string;
  onChange: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  onStop: () => void;
};

export function Composer({ message, disabled, status, onChange, onSubmit, onStop }: ComposerProps) {
  return (
    <form className="composer-shell" onSubmit={onSubmit}>
      <div className="composer-field">
        <textarea
          value={message}
          onChange={(event) => onChange(event.target.value)}
          placeholder="输入消息，支持对话、读取文件、总结仓库、执行工作流"
          disabled={disabled}
        />
      </div>
      <div className="composer-actions">
        <span className="composer-note">过程会以内联状态显示，最终回复保持主阅读体验。</span>
        {status === "streaming" || status === "running" ? (
          <button type="button" className="secondary-button" onClick={onStop}>
            停止
          </button>
        ) : (
          <button type="submit" className="primary-button" disabled={disabled || !message.trim()}>
            发送
          </button>
        )}
      </div>
    </form>
  );
}
