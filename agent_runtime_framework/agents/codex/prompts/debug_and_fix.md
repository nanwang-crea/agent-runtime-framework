debug_and_fix workflow (debug and repair):

**Step order:**
1. Understand the error message or problem description; confirm the target module/file scope.
2. Use `search_workspace_symbols` or `grep_workspace` to locate the function/variable definitions involved in the error.
3. Use `read_workspace_text` to read the relevant files and understand the context.
4. If there are test failures, run `run_tests` first to get the complete failure output, then locate the root cause.
5. Analyze the root cause (not just the symptom) and confirm the fix strategy.
6. Apply the fix following the change_and_verify protocol.
7. After fixing, re-run `run_tests` to confirm the problem is resolved.
8. Explain the root cause and what was changed.

**Prohibited:** do not fix symptoms while ignoring root causes; do not make speculative changes without understanding the code context.
