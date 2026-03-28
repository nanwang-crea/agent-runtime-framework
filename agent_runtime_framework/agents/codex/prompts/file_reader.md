file_reader workflow:
- Resolve the file target first.
- If the user wants a summary, explanation, or key points, prefer reading an excerpt rather than relying on a coarse summary.
- If the user explicitly asks to see the full content, read the complete text.
- Synthesize the answer from the excerpt or full text — do not relay raw tool output as the final response.

Example:
- Goal: "Summarize README.md"
- Good tool sequence: `resolve_workspace_target` -> `read_workspace_excerpt` or `read_workspace_text` -> `respond`

Example:
- Goal: "Show me the full contents of pyproject.toml"
- Good tool sequence: `resolve_workspace_target` -> `read_workspace_text` -> `respond`
