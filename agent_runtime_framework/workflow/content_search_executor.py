from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

from agent_runtime_framework.workflow.llm_access import get_workspace_root
from typing import Any

from agent_runtime_framework.workflow.runtime_protocols import RuntimeContextLike

from agent_runtime_framework.workflow.llm_synthesis import synthesize_text
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class ContentSearchExecutor:
    max_candidates: int = 40
    max_file_chars: int = 6000
    context_radius: int = 1

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        runtime_context = dict(context or {})
        workspace_root = Path(get_workspace_root(runtime_context, ".")).resolve()
        resolved_target = dict(run.shared_state.get("resolved_target") or {})
        search_plan = dict(run.shared_state.get("search_plan") or {})
        if not search_plan:
            return NodeResult(status=NODE_STATUS_FAILED, error="Missing search_plan")
        if bool(resolved_target.get("clarification_required")):
            summary = str(resolved_target.get("text") or resolved_target.get("summary") or "Please clarify the target.")
            return NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={
                    "summary": summary,
                    "matches": [],
                    "candidates": [],
                    "ranked_targets": [],
                    "evidence_items": [],
                    "clarification_required": True,
                },
                references=[],
            )

        terms = [str(item).strip() for item in search_plan.get("semantic_queries", []) or [] if str(item).strip()]
        if not terms:
            return NodeResult(status=NODE_STATUS_FAILED, error="search_plan missing semantic_queries")
        symbol_hint = ""
        candidates = self._candidate_paths(search_plan)
        path_bias = [str(item).strip() for item in search_plan.get("path_bias", []) or [] if str(item).strip()]
        if path_bias:
            candidates = list(dict.fromkeys([*path_bias, *candidates]))
        must_avoid = [str(item).strip() for item in search_plan.get("must_avoid", []) or [] if str(item).strip()]
        matches: list[dict[str, Any]] = []
        evidence_items: list[dict[str, Any]] = []
        references: list[str] = []

        for candidate in candidates[: self.max_candidates]:
            path = Path(candidate)
            if not path.is_absolute():
                path = (workspace_root / candidate).resolve()
            if not path.exists():
                continue
            relative_path = self._relative_path(path, workspace_root)
            if must_avoid and any(relative_path.startswith(item) for item in must_avoid):
                continue
            path_text = str(path).lower()
            if path.is_dir():
                score = self._path_score(path, terms, prefer_code=bool(symbol_hint))
                if score <= 0:
                    continue
                match = {
                    "path": str(path),
                    "relative_path": relative_path,
                    "score": score,
                    "matched_terms": [term for term in terms if term in path_text],
                    "kind": "directory",
                }
                matches.append(match)
                evidence_items.append(
                    {
                        "kind": "search_hit",
                        "path": str(path),
                        "relative_path": relative_path,
                        "summary": f"Matched search terms in {relative_path}",
                        "score": score,
                    }
                )
                references.append(str(path))
                continue
            file_text = path.read_text(encoding="utf-8", errors="ignore")[: self.max_file_chars]
            path_score = self._path_score(path, terms, prefer_code=bool(symbol_hint))
            text_score = self._text_score(file_text, terms, symbol_hint=symbol_hint)
            line_hit = self._best_line_hit(file_text, terms, symbol_hint=symbol_hint)
            score = path_score * 2 + text_score + int(line_hit.get("score") or 0)
            if score <= 0:
                continue
            match = {
                "path": str(path),
                "relative_path": relative_path,
                "score": score,
                "matched_terms": [term for term in terms if term in path.name.lower() or term in file_text.lower()],
                "kind": "file",
                **({"line": int(line_hit["line"]), "context": str(line_hit["context"])} if line_hit else {}),
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
        )
        if summary is None:
            raise RuntimeError("composer model unavailable for content_search summary")
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={
                "summary": summary,
                "matches": matches,
                "candidates": matches,
                "ranked_targets": matches,
                "evidence_items": evidence_items,
                "artifacts": {"search_terms": terms},
                "quality_signals": [
                    {
                        "source": "content_search",
                        "relevance": "high" if matches else "low",
                        "confidence": 0.8 if matches else 0.2,
                        "progress_contribution": "candidate_identified" if matches else "no_relevant_candidate",
                        "verification_needed": False,
                        "recoverable_error": False,
                    }
                ],
                "reasoning_trace": [
                    {
                        "kind": "search_strategy",
                        "summary": f"Searched {len(candidates[: self.max_candidates])} candidate paths using {len(terms)} terms",
                    },
                    *(
                        [
                            {
                                "kind": "top_match",
                                "summary": f"Top ranked target is {matches[0]['relative_path']} with score {matches[0]['score']}",
                            }
                        ]
                        if matches
                        else []
                    ),
                ],
            },
            references=list(dict.fromkeys(references)),
        )

    def _candidate_paths(self, search_plan: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        for key in ("path_bias", "candidate_paths"):
            for value in search_plan.get(key, []) or []:
                text = str(value or "").strip()
                if text:
                    candidates.append(text)
        return list(dict.fromkeys(candidates))

    def _path_score(self, path: Path, terms: list[str], *, prefer_code: bool) -> int:
        haystack = str(path).lower()
        score = sum(1 for term in terms if term in haystack)
        suffix = path.suffix.lower()
        if prefer_code and suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs"}:
            score += 4
        elif suffix in {".md", ".txt", ".rst"}:
            score += 1
        parts = {part.lower() for part in path.parts}
        if prefer_code and parts & {"src", "lib", "app", "agent_runtime_framework"}:
            score += 3
        direct_name_hit = any(term in path.name.lower() for term in terms)
        if parts & {"docs", "doc"} and not direct_name_hit:
            score -= 1
        if parts & {"tests", "test"} and not direct_name_hit:
            score -= 1
        return max(score, 1 if direct_name_hit else score)

    def _text_score(self, text: str, terms: list[str], *, symbol_hint: str) -> int:
        lowered = text.lower()
        score = sum(lowered.count(term) for term in terms)
        if symbol_hint:
            score += lowered.count(symbol_hint.lower()) * 4
        return score

    def _best_line_hit(self, text: str, terms: list[str], *, symbol_hint: str) -> dict[str, Any]:
        lines = text.splitlines()
        best: dict[str, Any] = {}
        best_score = 0
        lowered_symbol = symbol_hint.lower() if symbol_hint else ""
        for index, line in enumerate(lines, start=1):
            lowered = line.lower()
            score = sum(1 for term in terms if term in lowered)
            if lowered_symbol and lowered_symbol in lowered:
                score += 4
            if score <= 0:
                continue
            if score > best_score:
                start = max(1, index - self.context_radius)
                end = min(len(lines), index + self.context_radius)
                context = "\n".join(lines[start - 1:end]).rstrip()
                best = {"line": index, "context": context, "score": score}
                best_score = score
        return best

    def _relative_path(self, path: Path, workspace_root: Path) -> str:
        try:
            return str(path.relative_to(workspace_root))
        except ValueError:
            return str(path)
