# Workflow 术语归一（2026-04-01）

## 当前统一术语

- `workspace_discovery`：工作区级浏览、结构概览、目录梳理。
- `workspace_read`：对单个明确目标做读取。
- `compound_read`：同时需要工作区概览与目标读取。
- `target_resolution`：先做目标消歧或实体定位。
- `content_search`：基于目标、路径、术语、symbol hint 召回候选内容。
- `chunked_file_read`：按窗口阅读具体文件或目录。
- `evidence_synthesis`：把 facts / evidence_items / chunks 汇总成证据结论。

## 旧术语映射

- `repository_explainer` -> `workspace_discovery`
- `file_reader` -> `workspace_read` 或 `chunked_file_read`（视层级而定）
- `response_synthesis` -> `evidence_synthesis`
- `file_inspection` -> `content_search` + `chunked_file_read`

## 目录约定

- 计划文档统一放在 `docs/plans/`。
- `docs/plan/` 不再继续使用。
