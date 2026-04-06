from __future__ import annotations


def build_goal_analysis_system_prompt() -> str:
    return (
        "You analyze a user goal for a workflow runtime. "
        "Return JSON only with keys: primary_intent, requires_repository_overview, "
        "requires_file_read, requires_final_synthesis, target_paths, metadata. "
        "Allowed primary_intent values are: generic, repository_overview, file_read, compound, "
        "target_explainer, change_and_verify, dangerous_change. "
        "Use file_read when the user explicitly names a concrete file path to read, summarize, explain, or inspect. "
        "Use repository_overview when the user asks about workspace structure or directory contents only. "
        "Use compound when the user asks for both workspace overview and file evidence, or multiple read-oriented evidence gathering steps. "
        "Use target_explainer only when the target is ambiguous and must be resolved before reading. "
        "Use change_and_verify when the user asks to edit, modify, create, refactor, or update files and expects the result to be checked. "
        "Use dangerous_change for destructive requests such as delete or remove. "
        "Populate metadata.requires_verification for change_and_verify when verification is requested."
    )


def build_decomposition_system_prompt() -> str:
    return (
        "You decompose a workflow goal into ordered subtasks. "
        "Return JSON only with key subtasks. Each subtask needs: task_id, task_profile, target, depends_on, metadata. "
        "Prefer existing graph-native task_profile values such as workspace_discovery, content_search, "
        "chunked_file_read, evidence_synthesis, verification, target_resolution. "
        "Do not invent unnecessary subtasks for simple read requests."
    )


def build_subgraph_planner_system_prompt() -> str:
    return (
        "You plan a workflow subgraph. Return JSON only with keys: planner_summary, nodes. "
        "Each node must contain node_id, node_type, reason, inputs, depends_on, success_criteria. "
        "Allowed node types include target_resolution, workspace_discovery, content_search, chunked_file_read, "
        "workspace_subtask, tool_call, verification_step, aggregate_results, evidence_synthesis. "
        "Prefer graph-native nodes first. Use target_resolution when the target is ambiguous. "
        "Use workspace_subtask only when the request is not yet well represented by graph-native nodes."
    )
