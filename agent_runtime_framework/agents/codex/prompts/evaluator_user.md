Goal: {{goal}}
Workflow: {{workflow_name}}
{{run_context_block}}

Task progress summary:
{{progress_summary}}

Recent completed actions:
{{recent_completed_actions}}

Available tools: {{available_tools}}
Current persona evidence_threshold: {{evidence_threshold}}

Return finish only if the workflow is complete and the user could receive the answer right now without another tool call or synthesis step.
Return continue if any required evidence gathering, synthesis, or verification still remains.
If you are uncertain, output abstain.
