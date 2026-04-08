import type { FormEvent } from "react";
import type { ModelsResponse } from "../../types";

type ComposerProps = {
  message: string;
  disabled: boolean;
  status: string;
  models: ModelsResponse;
  selectedInstance: string;
  selectedModel: string;
  onChange: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  onStop: () => void;
  onSelectInstance: (instance: string) => void;
  onSelectModel: (model: string) => void;
  onApplyModel: () => void;
};

export function Composer({
  message,
  disabled,
  status,
  models,
  selectedInstance,
  selectedModel,
  onChange,
  onSubmit,
  onStop,
  onSelectInstance,
  onSelectModel,
  onApplyModel,
}: ComposerProps) {
  const availableModels = models.instances.find((item) => item.instance === selectedInstance)?.models || [];

  return (
    <form className="composer-shell" onSubmit={onSubmit}>
      <div className="composer-field">
        <textarea
          value={message}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder="给 Agent 一个任务，或者继续当前对话"
          disabled={disabled}
        />
      </div>
      <div className="composer-actions">
        <div className="composer-toolbar">
          <span className="composer-note">本地</span>
          <span className="composer-note">默认权限</span>
          <span className="composer-note">{status === "streaming" ? "正在生成" : "就绪"}</span>
        </div>
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

      <div className="composer-bottom-row">
        <div className="composer-compact-models">
          <span className="composer-inline-label">模型</span>
          <select className="composer-compact-select" value={selectedInstance} onChange={(event) => onSelectInstance(event.target.value)}>
            <option value="">实例</option>
            {models.instances.map((item) => (
              <option key={item.instance} value={item.instance}>
                {item.instance}
              </option>
            ))}
          </select>
          <select className="composer-compact-select" value={selectedModel} onChange={(event) => onSelectModel(event.target.value)}>
            <option value="">模型</option>
            {availableModels.map((model) => (
              <option key={`${selectedInstance}-${model.model_name}`} value={model.model_name}>
                {model.display_name}
              </option>
            ))}
          </select>
          <button type="button" className="composer-apply-link" onClick={onApplyModel} disabled={!selectedInstance || !selectedModel}>
            应用
          </button>
        </div>
        <span className="composer-enter-hint">Enter 发送，Shift+Enter 换行</span>
      </div>
    </form>
  );
}
