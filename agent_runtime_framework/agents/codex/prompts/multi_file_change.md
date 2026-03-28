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
