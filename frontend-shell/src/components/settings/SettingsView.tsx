import type { ConfigResponse, ModelsResponse } from "../../types";

type SettingsViewProps = {
  config: ConfigResponse;
  models: ModelsResponse;
  globalModelDraft: { instance: string; model: string };
  instanceDrafts: Record<string, { apiKey: string; baseUrl: string; wireApi: string }>;
  defaultWireApi: string;
  onGlobalInstanceChange: (instance: string) => void;
  onGlobalModelChange: (model: string) => void;
  onApplyGlobalModel: () => void;
  onUpdateDraft: (instanceId: string, key: "apiKey" | "baseUrl" | "wireApi", value: string) => void;
  onSaveInstance: (instanceId: string) => void;
  onAuthInstance: (instanceId: string) => void;
  onSetCurrentModel: (instanceId: string) => void;
};

export function SettingsView({
  config,
  models,
  globalModelDraft,
  instanceDrafts,
  defaultWireApi,
  onGlobalInstanceChange,
  onGlobalModelChange,
  onApplyGlobalModel,
  onUpdateDraft,
  onSaveInstance,
  onAuthInstance,
  onSetCurrentModel,
}: SettingsViewProps) {
  const selectedRuntimeInstance = models.instances.find((item) => item.instance === globalModelDraft.instance);

  return (
    <section className="settings-view">
      <div className="settings-hero-card">
        <div>
          <span className="conversation-label">Global Runtime</span>
          <strong>
            {models.active_model.instance || "未选择实例"} / {models.active_model.model_name || "未选择模型"}
          </strong>
          <p>设置页和对话页使用同一套轻量工作台风格，只保留真正需要操作的模型与连接配置。</p>
        </div>
        <span className="header-chip">{config.path || "未加载配置文件"}</span>
      </div>

      <div className="settings-card">
        <div className="settings-card-head">
          <div>
            <span className="conversation-label">当前生效模型</span>
            <strong>全局模型切换</strong>
            <p className="instance-meta-text">聊天输入区也可以直接切换模型，这里保留为设置入口。</p>
          </div>
        </div>
        <div className="settings-form-row">
          <select value={globalModelDraft.instance} onChange={(event) => onGlobalInstanceChange(event.target.value)}>
            <option value="">选择实例</option>
            {models.instances.map((item) => (
              <option key={item.instance} value={item.instance}>
                {item.instance}
              </option>
            ))}
          </select>
          <select value={globalModelDraft.model} onChange={(event) => onGlobalModelChange(event.target.value)}>
            <option value="">选择模型</option>
            {(selectedRuntimeInstance?.models || []).map((model) => (
              <option key={`${selectedRuntimeInstance?.instance}-${model.model_name}`} value={model.model_name}>
                {model.display_name}
              </option>
            ))}
          </select>
          <button type="button" className="primary-button" onClick={onApplyGlobalModel}>
            应用
          </button>
        </div>
      </div>

      <div className="settings-instance-list">
        {config.instances.map((instanceConfig) => {
          const draft = instanceDrafts[instanceConfig.instance] || {
            apiKey: "",
            baseUrl: instanceConfig.base_url || "",
            wireApi: instanceConfig.wire_api || defaultWireApi,
          };
          const runtimeInstance = models.instances.find((item) => item.instance === instanceConfig.instance);
          return (
            <div key={instanceConfig.instance} className="settings-card instance-settings-card">
              <div className="settings-card-head">
                <div>
                  <strong>{instanceConfig.instance}</strong>
                  <span>{instanceConfig.type}</span>
                </div>
                <span className={`header-chip ${runtimeInstance?.authenticated ? "status-ready" : ""}`}>
                  {runtimeInstance?.authenticated ? "authenticated" : "not configured"}
                </span>
              </div>

              <p className="instance-meta-text">
                {runtimeInstance?.auth_error || `base URL: ${instanceConfig.base_url || "未配置"} · wire API: ${instanceConfig.wire_api || defaultWireApi}`}
              </p>

              <div className="settings-form-grid">
                <input
                  value={draft.apiKey}
                  onChange={(event) => onUpdateDraft(instanceConfig.instance, "apiKey", event.target.value)}
                  placeholder={`${instanceConfig.instance} API key`}
                />
                <input
                  value={draft.baseUrl}
                  onChange={(event) => onUpdateDraft(instanceConfig.instance, "baseUrl", event.target.value)}
                  placeholder={instanceConfig.base_url || "base URL"}
                />
                <select
                  value={draft.wireApi}
                  onChange={(event) => onUpdateDraft(instanceConfig.instance, "wireApi", event.target.value)}
                >
                  <option value="chat_completions">chat_completions</option>
                  <option value="responses">responses</option>
                </select>
              </div>

              <div className="settings-actions">
                <button type="button" className="primary-button" onClick={() => onSaveInstance(instanceConfig.instance)}>
                  保存
                </button>
                <button type="button" className="secondary-button" onClick={() => onAuthInstance(instanceConfig.instance)}>
                  认证
                </button>
                <button type="button" className="secondary-button" onClick={() => onSetCurrentModel(instanceConfig.instance)}>
                  设为当前模型
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
