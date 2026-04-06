# Workspace Write Nodes

This note freezes the public graph-native write-node taxonomy used by the workflow runtime.

## First-Class Node Types

- `create_path`
- `move_path`
- `delete_path`
- `apply_patch`
- `write_file`
- `append_text`
- `verification`

## Public Architecture Rules

- Node names express workflow-stage intent, not raw tool names.
- Tools remain lower-level execution primitives behind the graph runtime.
- The graph does not mirror workspace tools one-to-one.
- Filesystem and text-edit changes are moving to graph-native nodes.
- `workspace_subtask` is temporary during migration and scheduled for removal.
- `workspace_subtask` is being removed, not expanded.

## Mapping Principle

The workflow runtime keeps fine-grained workspace tools as the execution layer. Graph nodes stay at the workflow level so planning can describe user-facing intent such as path creation, path movement, file rewriting, patch application, text appends, and post-change verification.
