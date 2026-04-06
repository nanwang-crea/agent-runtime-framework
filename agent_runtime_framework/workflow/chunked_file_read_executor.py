from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_runtime_framework.workflow.llm_access import get_workspace_root
from typing import Any

from agent_runtime_framework.workflow.runtime_protocols import RuntimeContextLike

from agent_runtime_framework.workflow.llm_synthesis import synthesize_text
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class ChunkedFileReadExecutor:
    max_chars: int = 4000
    window_radius: int = 8

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        runtime_context = dict(context or {})
        workspace_root = Path(get_workspace_root(runtime_context, ".")).resolve()
        read_plan = dict(run.shared_state.get("read_plan") or {})
        target_path = str(node.metadata.get("target_path") or read_plan.get("target_path") or "").strip()
        matched_lines: list[int] = []
        clarification_summary = ""

        node_results = run.shared_state.get("node_results", {})
        for result in node_results.values():
            if not isinstance(result.output, dict):
                continue
            if bool(result.output.get("clarification_required")):
                clarification_summary = str(result.output.get("summary") or result.output.get("text") or "Please clarify the target.")
            for match in result.output.get("matches", []) or []:
                if isinstance(match, dict) and match.get("line") is not None:
                    matched_lines.append(int(match["line"]))
                if not target_path and isinstance(match, dict) and match.get("path"):
                    target_path = str(match["path"])
            if not target_path:
                ranked_targets = result.output.get("ranked_targets", []) or []
                if ranked_targets and isinstance(ranked_targets[0], dict) and ranked_targets[0].get("path"):
                    target_path = str(ranked_targets[0]["path"])

        if clarification_summary:
            return NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={"summary": clarification_summary, "chunks": [], "evidence_items": [], "clarification_required": True, "facts": []},
                references=[],
            )

        if not target_path:
            return NodeResult(status=NODE_STATUS_FAILED, error="Missing target_path")

        path = Path(target_path)
        if not path.is_absolute():
            path = (workspace_root / target_path).resolve()
        if not path.exists():
            return NodeResult(status=NODE_STATUS_FAILED, error=f"Target file not found: {target_path}")
        if path.is_dir():
            entries = self._directory_entries(path)
            chunk_text = "\n".join(entries).rstrip()
            summary = f"Directory listing for {self._relative_path(path, workspace_root)}"
            return NodeResult(
                status=NODE_STATUS_COMPLETED,
                output={
                    "summary": summary,
                    "path": self._relative_path(path, workspace_root),
                    "chunks": [{"start_line": 1, "end_line": len(entries), "text": chunk_text}] if entries else [],
                    "evidence_items": [{
                        "kind": "directory_listing",
                        "path": str(path),
                        "relative_path": self._relative_path(path, workspace_root),
                        "summary": summary,
                    }],
                    "artifacts": {"read_mode": "directory_listing"},
                    "facts": [{"kind": "directory", "path": self._relative_path(path, workspace_root)}],
                    "quality_signals": [
                        {
                            "source": "chunked_file_read",
                            "relevance": "medium",
                            "confidence": 0.75,
                            "progress_contribution": "grounded_evidence_collected",
                            "verification_needed": False,
                            "recoverable_error": False,
                        }
                    ],
                    "reasoning_trace": [
                        {
                            "kind": "read_strategy",
                            "summary": f"Read directory listing for {self._relative_path(path, workspace_root)}",
                        }
                    ],
                },
                references=[str(path)],
            )

        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        chunks, has_more = self._build_chunks(lines, matched_lines, preferred_regions=[str(item).strip() for item in read_plan.get("preferred_regions", []) or [] if str(item).strip()])
        evidence_items = [
            {
                "kind": "file_chunk",
                "path": str(path),
                "relative_path": self._relative_path(path, workspace_root),
                "summary": f"Lines {chunk['start_line']}-{chunk['end_line']} from {path.name}",
                "line_start": chunk["start_line"],
                "line_end": chunk["end_line"],
            }
            for chunk in chunks
        ]
        summary = synthesize_text(
            context,
            role="composer",
            system_prompt=(
                "You summarize file excerpts for an end user. "
                "Describe what the selected chunks are about in one concise paragraph."
            ),
            payload={"goal": run.goal, "path": str(path), "chunks": chunks},
            max_tokens=220,
        )
        if summary is None:
            raise RuntimeError("composer model unavailable for chunked_file_read summary")
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={
                "summary": summary,
                "path": self._relative_path(path, workspace_root),
                "chunks": chunks,
                "evidence_items": evidence_items,
                "artifacts": {
                    "read_mode": "windowed_by_hits" if matched_lines else "full_if_small" if len("\n".join(lines)) <= self.max_chars else "head_tail",
                    "page": int(node.metadata.get("page") or 1),
                    "has_more": has_more,
                },
                "facts": [],
                "quality_signals": [
                    {
                        "source": "chunked_file_read",
                        "relevance": "high" if chunks else "low",
                        "confidence": 0.85 if chunks else 0.3,
                        "progress_contribution": "grounded_evidence_collected" if chunks else "no_grounded_evidence",
                        "verification_needed": False,
                        "recoverable_error": False,
                    }
                ],
                "reasoning_trace": [
                    {
                        "kind": "read_strategy",
                        "summary": (
                            f"Read {len(chunks)} chunk(s) from {self._relative_path(path, workspace_root)} "
                            f"using {'matched line windows' if matched_lines else 'default chunking'}"
                        ),
                    }
                ],
            },
            references=[str(path)],
        )

    def _build_chunks(self, lines: list[str], matched_lines: list[int], *, preferred_regions: list[str] | None = None) -> tuple[list[dict[str, Any]], bool]:
        total_text = "\n".join(lines)
        preferred_regions = preferred_regions or []
        if len(total_text) <= self.max_chars:
            if "head" in preferred_regions and len(lines) > 0:
                end_line = min(len(lines), max(1, self.window_radius))
                return [self._chunk(lines, 1, end_line)], len(lines) > end_line
            return [self._chunk(lines, 1, len(lines))], False
        if len(lines) == 1:
            return [{"start_line": 1, "end_line": 1, "text": f"{total_text[: self.max_chars].rstrip()}\n...[已截断]"}], True
        if "head" in preferred_regions:
            end_line = min(len(lines), max(1, self.window_radius))
            return [self._chunk(lines, 1, end_line)], len(lines) > end_line
        if "tail" in preferred_regions:
            start_line = max(1, len(lines) - self.window_radius + 1)
            return [self._chunk(lines, start_line, len(lines))], start_line > 1
        if matched_lines:
            windows: list[tuple[int, int]] = []
            for center in sorted(set(matched_lines)):
                start_line = max(1, center - self.window_radius)
                end_line = min(len(lines), center + self.window_radius)
                if windows and start_line <= windows[-1][1] + 1:
                    windows[-1] = (windows[-1][0], max(windows[-1][1], end_line))
                else:
                    windows.append((start_line, end_line))
            return [self._chunk(lines, start, end) for start, end in windows], False
        head_count = min(len(lines), max(1, self.window_radius))
        tail_start = max(1, len(lines) - self.window_radius + 1)
        chunks = [self._chunk(lines, 1, head_count)]
        if tail_start > head_count:
            chunks.append(self._chunk(lines, tail_start, len(lines)))
        return chunks, tail_start > head_count

    def _chunk(self, lines: list[str], start_line: int, end_line: int) -> dict[str, Any]:
        text = "\n".join(lines[start_line - 1:end_line]).rstrip()
        return {"start_line": start_line, "end_line": end_line, "text": text}

    def _directory_entries(self, path: Path) -> list[str]:
        entries: list[str] = []
        for child in sorted(path.rglob("*")):
            if len(entries) >= 40:
                entries.append("...[已截断]")
                break
            suffix = "/" if child.is_dir() else ""
            entries.append(str(child.relative_to(path)) + suffix)
        return entries

    def _relative_path(self, path: Path, workspace_root: Path) -> str:
        try:
            return str(path.relative_to(workspace_root))
        except ValueError:
            return str(path)
