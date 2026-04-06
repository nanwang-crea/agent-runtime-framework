from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

from agent_runtime_framework.workflow.llm_access import get_workspace_root
from typing import Any

from agent_runtime_framework.workflow.runtime_protocols import RuntimeContextLike

from agent_runtime_framework.workflow.llm_synthesis import synthesize_text
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class ContentSearchExecutor:
    max_candidates: int = 40
    max_file_chars: int = 6000
    context_radius: int = 1

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        runtime_context = dict(context or {})
        workspace_root = Path(get_workspace_root(runtime_context, ".")).resolve()
        resolved_target = dict(run.shared_state.get("resolved_target") or {})
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

        terms = self._search_terms(run.goal, node.metadata)
        symbol_hint = str(node.metadata.get("symbol_hint") or "").strip()
        candidates = self._candidate_paths(run, node.metadata)
        matches: list[dict[str, Any]] = []
        evidence_items: list[dict[str, Any]] = []
        references: list[str] = []

        for candidate in candidates[: self.max_candidates]:
            path = Path(candidate)
            if not path.is_absolute():
                path = (workspace_root / candidate).resolve()
            if not path.exists():
                continue
            path_text = str(path).lower()
            relative_path = self._relative_path(path, workspace_root)
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
            },
            references=list(dict.fromkeys(references)),
        )

    def _candidate_paths(self, run: WorkflowRun, metadata: dict[str, Any] | None = None) -> list[str]:
        node_results = run.shared_state.get("node_results", {})
        candidates: list[str] = []
        metadata = dict(metadata or {})
        for key in ("target_path", "target_hint"):
            value = str(metadata.get(key) or "").strip()
            if value:
                candidates.append(value)
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
        terms: list[str] = []
        for raw in [metadata.get("target_hint"), metadata.get("target_path"), metadata.get("symbol_hint"), goal]:
            value = str(raw or "").strip()
            if not value:
                continue
            normalized = value.lower().replace("/", " ").replace("_", " ")
            terms.extend(part for part in normalized.split() if len(part) >= 2)
            terms.extend(token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]+", value) if len(token) >= 2)
        return list(dict.fromkeys(terms))

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
