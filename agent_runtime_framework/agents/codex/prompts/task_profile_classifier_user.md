User request: {{user_input}}
{{run_context_block}}

Profile selection rules:
- repository_explainer: listing files/directories, exploring workspace/repo structure, asking what files exist or what a directory contains.
- file_reader: reading, summarizing, or explaining a specific file's content.
- debug_and_fix: error, bug, crash, exception, debugging, or fix request.
- multi_file_change: refactoring, batch edits, updating all call sites, renaming across files.
- change_and_verify: editing a file, applying a patch, creating/deleting/moving a file, or a single-file change.
- test_and_verify: running tests, checking test results, fixing test failures.
- chat: general question, explanation, or anything that does not require workspace changes or file access.

Output only JSON.
