from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime_framework.workflow.llm_synthesis import synthesize_text
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class ContentSearchExecutor:
    max_candidates: int = 40
    max_file_chars: int = 6000

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        runtime_context = dict(context or {})
        workspace_root = Path(str(runtime_context.get("workspace_root", "."))).resolve()
        terms = self._search_terms(run.goal, node.metadata)
        candidates = self._candidate_paths(run)
        matches: list[dict[str, Any]] = []
        evidence_items: list[dict[str, Any]] = []
        references: list[str] = []

        for candidate in candidates[: self.max_candidates]:
            path = Path(candidate)
            if not path.is_absolute():
                path = (workspace_root / candidate).resolve()
            if not path.exists() or path.is_dir():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")[: self.max_file_chars]
            path_score = self._path_score(path, terms)
            text_score = self._text_score(text, terms)
            score = path_score * 2 + text_score
            if score <= 0:
                continue
            match = {
                "path": str(path),
                "relative_path": self._relative_path(path, workspace_root),
                "score": score,
                "matched_terms": [term for term in terms if term in path.name.lower() or term in text.lower()],
            }
            matches.append(match)
            evidence_items.append(
                {
                    "kind": "search_hit",
                    "path": str(path),
                    "relative_path": match["relative_path"],
                    "summary": f"Matched search terms in {match['relative_path']}",
                    "score": score,
                }
            )
            references.append(str(path))

        matches.sort(key=lambda item: (-float(item["score"]), item["relative_path"]))
        summary = synthesize_text(
            context,
            role="composer",
            system_prompt=(
                "You summarize search results for an end user. "
                "Mention the top matching targets and why they look relevant in one concise paragraph."
            ),
            payload={"goal": run.goal, "terms": terms, "matches": matches[:10]},
            max_tokens=180,
        ) or self._fallback_summary(matches)
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={
                "summary": summary,
                "matches": matches,
                "ranked_targets": matches,
                "evidence_items": evidence_items,
                "artifacts": {"search_terms": terms},
            },
            references=list(dict.fromkeys(references)),
        )

    def _candidate_paths(self, run: WorkflowRun) -> list[str]:
        node_results = run.shared_state.get("node_results", {})
        candidates: list[str] = []
        for result in node_results.values():
            if not isinstance(result.output, dict):
                continue
            for item in result.output.get("evidence_items", []) or []:
                if isinstance(item, dict) and item.get("path"):
                    candidates.append(str(item["path"]))
            for reference in result.references:
                candidates.append(reference)
        return list(dict.fromkeys(candidates))

    def _search_terms(self, goal: str, metadata: dict[str, Any]) -> list[str]:
        terms = []
        target_hint = str(metadata.get("target_hint") or metadata.get("target_path") or "").strip().lower()
        if target_hint:
            terms.extend(part for part in target_hint.replace("/", " ").replace("_", " ").split() if part)
        normalized_goal = str(goal or "").lower().replace("/", " ").replace("_", " ")
        terms.extend(part for part in normalized_goal.split() if len(part) >= 2)
        return list(dict.fromkeys(terms))

    def _path_score(self, path: Path, terms: list[str]) -> int:
        haystack = str(path).lower()
        return sum(1 for term in terms if term in haystack)

    def _text_score(self, text: str, terms: list[str]) -> int:
        lowered = text.lower()
        return sum(lowered.count(term) for term in terms)

    def _relative_path(self, path: Path, workspace_root: Path) -> str:
        try:
            return str(path.relative_to(workspace_root))
        except ValueError:
            return str(path)

    def _fallback_summary(self, matches: list[dict[str, Any]]) -> str:
        if not matches:
            return "No matching files found."
        return "；".join(item["relative_path"] for item in matches[:5])
