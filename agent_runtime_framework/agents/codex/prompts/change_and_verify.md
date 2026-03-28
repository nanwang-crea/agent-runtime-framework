change_and_verify workflow (edit and verify):

**Step order:**
1. Use `resolve_workspace_target` to confirm the target path.
2. Use `read_workspace_text` or `read_workspace_excerpt` to read the target file and understand the existing implementation.
3. If modifying a function or class, use `search_workspace_symbols` or `grep_workspace` to find all call sites and assess impact scope.
4. Choose the minimum-change primitive:
   - Precise replacement → `apply_text_patch` or `replace_workspace_text`
   - Local append → `append_workspace_text`
   - Full rewrite → `edit_workspace_text` (last resort)
5. After editing, use `read_workspace_text` to read back and confirm the change is correct.
6. Run `run_tests` to verify the change does not break existing behavior.
7. If tests fail, analyze the failure and continue fixing — do not stop.
8. Use `get_git_diff` to show a summary of all changes, including what was modified and the verification result.

**For multi-file changes:** complete all edits first, then run `run_tests` once — do not run tests file-by-file.

**Prohibited:** do not write to a file without reading its current content first; do not skip verification.

Example:
- Goal: "Rename a config flag in settings.py and make sure tests still pass"
- Good tool sequence: `resolve_workspace_target` -> `read_workspace_text` -> `search_workspace_symbols` or `grep_workspace` -> `apply_text_patch` -> `read_workspace_text` -> `run_tests` -> `respond`

Example:
- Goal: "Update the CLI help text in one file"
- Good tool sequence: `resolve_workspace_target` -> `read_workspace_text` -> `apply_text_patch` -> `read_workspace_text` -> `run_tests` or lightweight verification -> `respond`
