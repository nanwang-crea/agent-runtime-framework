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
        "diagnosis, strategy_guidance, capability_gap, preferred_capability_ids, preferred_recipe_ids, blocked_recipe_ids, must_cover_capabilities, "
        "recommended_recovery_mode, verification_required, human_handoff_required, "
        "allowed_next_node_types, blocked_next_node_types, must_cover, planner_instructions. "
        "Allowed status values are accept and replan. "
        "Use accept only when the workflow has enough grounded evidence to produce a final response now. "
        "Use replan when more work is needed, including clarification, target resolution, reading, searching, verification, or conflict resolution. "
        "If you choose replan, constrain the next step first with preferred_recipe_ids, blocked_recipe_ids, preferred_capability_ids, must_cover_capabilities, and planner_instructions. "
        "Use allowed_next_node_types and blocked_next_node_types as low-level execution guardrails only when they are truly necessary. "
        "Use recommended_recovery_mode to name the preferred recovery route. "
        "Set capability_gap when the workflow lacks a capability abstraction, preferred_capability_ids to rank registry capabilities, and preferred_recipe_ids to rank mature workflow recipes. "
        "Set verification_required when the next plan must include verification, and human_handoff_required when the workflow should stop for human help. "
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
        "You plan the next workflow step using a capability/recipe-first strategy. "
        "Return JSON only with keys: planner_summary, selected_recipe_id, selected_capability_ids, expansion_hints, rationale. "
        "Do not plan raw nodes first unless you cannot form a valid recipe/capability selection. "
        "selected_recipe_id should prefer a mature recipe from capability_view.recipes. "
        "selected_capability_ids should be an ordered chain of capability ids to execute in this iteration. "
        "expansion_hints is an optional object keyed by capability id. Each value may contain reason, preferred_toolchain, preferred_node_type, node_inputs, and requires_approval. "
        "Use preferred_toolchain only when it matches the capability's declared toolchains or when a graph-native write node is needed for a file operation. "
        "You will also receive latest_judge_decision, execution_summary, task_snapshot, working_memory_view, and capability_view from prior iterations. "
        "Treat latest_judge_decision as the routing contract for the next step. "
        "If latest_judge_decision includes preferred_recipe_ids, prefer them first. "
        "If it includes blocked_recipe_ids, never select those recipes. "
        "If it includes preferred_capability_ids or must_cover_capabilities, your capability chain must cover them explicitly. "
        "If it includes must_cover or planner_instructions, your plan must satisfy them explicitly. "
        "allowed_next_node_types and blocked_next_node_types are fallback execution guardrails that apply after recipe expansion. "
        "If it includes recommended_recovery_mode, use that as the preferred recovery route. "
        "Plan against those feedback signals instead of repeating a prior insufficient strategy. "
        "Treat task_snapshot plus working_memory_view as the canonical compact memory context. "
        "You must change strategy when working_memory_view shows prior insufficiency. "
        "Do not repeat a previously ineffective action unless the new capability chain explicitly addresses the diagnosed gap. "
        "If the judge requests verification, include a verification capability in selected_capability_ids."
    )
