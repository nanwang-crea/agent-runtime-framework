repository_overview workflow:
- Resolve the target first, then list the directory structure.
- For directory targets, observe the structure before selecting representative files.
- Prioritize README, configuration entry points, package entry points, and core files (main/app/service) as representative files.
- After reading or extracting outlines of representative files, provide a synthesized explanation of directory responsibilities and key module roles.

Example:
- Goal: "Explain the backend package structure under agent_runtime_framework/demo"
- Good tool sequence: `resolve_workspace_target` -> `inspect_workspace_path` -> `rank_workspace_entries` -> `extract_workspace_outline` (for top files) -> `respond`
- Why: first confirm the directory, then inspect the overall structure, then pick representative files before synthesizing the explanation.

Example:
- Goal: "What is in the frontend-shell folder?"
- Good tool sequence: `resolve_workspace_target` -> `inspect_workspace_path` -> `rank_workspace_entries` -> `read_workspace_text` on `README.md` if present -> `respond`
- Why: repository overviews should summarize the folder's structure and responsibilities, not dump raw directory listings.
