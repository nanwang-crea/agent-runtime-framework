test_and_verify workflow (run and verify tests):

**Step order:**
1. Run `run_tests` to get the current test state.
2. If all tests pass, report the result and finish.
3. If there are failures, analyze the failure output and locate the failing test files and functions.
4. Use `read_workspace_text` to read the failing test files and the code under test; understand the difference between expected and actual behavior.
5. Determine whether the issue is in the test itself or a bug in the production code.
6. Fix the root cause following the debug_and_fix or change_and_verify protocol.
7. Re-run `run_tests` after fixing and repeat until all tests pass.

**Prohibited:** do not modify tests just to make them pass without fixing the underlying code; do not make blind changes without understanding why a test fails.
