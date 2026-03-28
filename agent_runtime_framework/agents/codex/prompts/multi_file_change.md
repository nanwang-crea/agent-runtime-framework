multi_file_change workflow (cross-file refactor or batch edits):

**Step order:**
1. Understand the full impact scope of the change: use `grep_workspace` and `search_workspace_symbols` to find all affected files.
2. List all files to modify and the reason for each, and confirm the dependency order.
3. Apply changes file by file in dependency order (dependencies first, to avoid intermediate broken states).
4. After each file, use `read_workspace_text` to read back and confirm.
5. After all files are modified, run `run_tests` to verify everything together.
6. Use `get_git_diff` to show all changes with a summary of what was modified.

**For interface/signature changes:** all call sites must be updated in sync. Use `grep_workspace` to find every call site, update each one, then verify.

**Prohibited:** do not miss call site updates; do not run tests mid-way while only some files are changed (intermediate state will cause false failures).

Example:
- Goal: "Rename `build_plan` to `build_execution_plan` across the codex agent"
- Good tool sequence: `grep_workspace` -> `search_workspace_symbols` -> `read_workspace_text` on all affected files -> `apply_text_patch` across each file -> `read_workspace_text` readback -> `run_tests` -> `get_git_diff` -> `respond`

Example:
- Goal: "Add a new parameter to a shared helper used by multiple modules"
- Good tool sequence: `search_workspace_symbols` -> `grep_workspace` for call sites -> `read_workspace_text` on dependency files first -> patch all impacted files -> `run_tests` -> `respond`
