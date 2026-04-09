from __future__ import annotations


def build_goal_analysis_system_prompt() -> str:
    return (
        "You analyze a user goal for a workflow runtime. "
        "Return JSON only with keys: primary_intent, requires_target_interpretation, "
        "requires_search, requires_read, requires_verification, metadata. "
        "Allowed primary_intent values are: generic, repository_overview, file_read, compound, "
        "target_explainer, change_and_verify, dangerous_change. "
        "Use file_read when the user explicitly names a concrete file path to read, summarize, explain, or inspect. "
        "Use repository_overview when the user asks about workspace structure or directory contents only. "
        "Use compound when the user asks for both workspace overview and file evidence, or multiple read-oriented evidence gathering steps. "
        "Use target_explainer only when the target is ambiguous and must be resolved before reading. "
        "Use change_and_verify when the user asks to edit, modify, create, refactor, or update files and expects the result to be checked. "
        "Use dangerous_change for destructive requests such as delete or remove. "
        "Set requires_target_interpretation when the exact workspace target must be semantically resolved. "
        "Set requires_search when the task needs search before reading or answering. "
        "Set requires_read when direct grounded file or directory evidence is required. "
        "Set requires_verification when the final result must be checked."
    )


def build_judge_system_prompt() -> str:
    return (
        "You are the workflow judge and routing controller. Return JSON only. "
        "Allowed top-level keys are: status, reason, missing_evidence, coverage_report, replan_hint, "
        "diagnosis, strategy_guidance, allowed_next_node_types, blocked_next_node_types, must_cover, planner_instructions. "
        "Allowed status values are accept and replan. "
        "Use accept only when the workflow has enough grounded evidence to produce a final response now. "
        "Use replan when more work is needed, including clarification, target resolution, reading, searching, verification, or conflict resolution. "
        "If you choose replan, explicitly constrain the next step with allowed_next_node_types, blocked_next_node_types, must_cover, and planner_instructions. "
        "Do not output final_response in allowed_next_node_types unless status is accept. "
        "Ground your decision in the supplied goal, aggregated evidence, execution summary, and judge memory view."
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
        "Allowed node types include interpret_target, plan_search, plan_read, target_resolution, workspace_discovery, content_search, chunked_file_read, "
        "tool_call, clarification, verification, verification_step, aggregate_results, evidence_synthesis, "
        "create_path, move_path, delete_path, apply_patch, write_file, append_text. "
        "Prefer graph-native nodes first. Use target_resolution when the target is ambiguous. "
        "You will also receive latest_judge_decision, execution_summary, task_snapshot, and working_memory_view from prior iterations. "
        "Treat latest_judge_decision as the routing contract for the next step. "
        "If latest_judge_decision includes allowed_next_node_types, you must stay within that set. "
        "If it includes blocked_next_node_types, you must not emit those node types. "
        "If it includes must_cover or planner_instructions, your plan must satisfy them explicitly. "
        "Plan against those feedback signals instead of repeating a prior insufficient node. "
        "Treat task_snapshot plus working_memory_view as the canonical compact memory context. "
        "You must change strategy when working_memory_view shows prior insufficiency. "
        "Do not repeat a previously ineffective action unless the new node explicitly addresses the diagnosed gap. "
        "If the judge requests verification, include a verification-oriented node in the next subgraph."
    )
