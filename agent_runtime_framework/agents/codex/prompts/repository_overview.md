repository_overview workflow:
- Resolve the target first, then list the directory structure.
- For directory targets, observe the structure before selecting representative files.
- Prioritize README, configuration entry points, package entry points, and core files (main/app/service) as representative files.
- After reading or extracting outlines of representative files, provide a synthesized explanation of directory responsibilities and key module roles.
- Treat requests such as "这个目录是做什么的", "梳理一下这个文件夹", "看看这个 package 的职责分布", and "介绍下模块结构" as the same repository-overview task family.
- A good repository answer usually covers: overall responsibility, important subfolders/files, likely entry points, and why those files matter.
- If the target resolves to a file instead of a directory, switch to a file-reader style explanation rather than forcing a directory overview.

Example:
- Goal: "Explain the backend package structure under agent_runtime_framework/demo"
- Good tool sequence: `resolve_workspace_target` -> `inspect_workspace_path` -> `rank_workspace_entries` -> `extract_workspace_outline` (for top files) -> `respond`
- Why: first confirm the directory, then inspect the overall structure, then pick representative files before synthesizing the explanation.

Example:
- Goal: "What is in the frontend-shell folder?"
- Good tool sequence: `resolve_workspace_target` -> `inspect_workspace_path` -> `rank_workspace_entries` -> `read_workspace_text` on `README.md` if present -> `respond`
- Why: repository overviews should summarize the folder's structure and responsibilities, not dump raw directory listings.
