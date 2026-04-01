from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime_framework.workflow.llm_synthesis import synthesize_text
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NODE_STATUS_FAILED, NodeResult, WorkflowNode, WorkflowRun


@dataclass(slots=True)
class ChunkedFileReadExecutor:
    max_chars: int = 4000
    window_radius: int = 8

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: dict[str, Any] | None = None) -> NodeResult:
        runtime_context = dict(context or {})
        workspace_root = Path(str(runtime_context.get("workspace_root", "."))).resolve()
        target_path = str(node.metadata.get("target_path") or "").strip()
        matched_lines: list[int] = []

        node_results = run.shared_state.get("node_results", {})
        for result in node_results.values():
            if not isinstance(result.output, dict):
                continue
            for match in result.output.get("matches", []) or []:
                if isinstance(match, dict) and match.get("line") is not None:
                    matched_lines.append(int(match["line"]))
                    if not target_path and match.get("path"):
                        target_path = str(match["path"])
            if not target_path:
                ranked_targets = result.output.get("ranked_targets", []) or []
                if ranked_targets and isinstance(ranked_targets[0], dict) and ranked_targets[0].get("path"):
                    target_path = str(ranked_targets[0]["path"])

        if not target_path:
            return NodeResult(status=NODE_STATUS_FAILED, error="Missing target_path")

        path = Path(target_path)
        if not path.is_absolute():
            path = (workspace_root / target_path).resolve()
        if not path.exists() or path.is_dir():
            return NodeResult(status=NODE_STATUS_FAILED, error=f"Target file not found: {target_path}")

        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        chunks = self._build_chunks(lines, matched_lines)
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
        ) or self._fallback_summary(path, chunks)
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={
                "summary": summary,
                "path": self._relative_path(path, workspace_root),
                "chunks": chunks,
                "evidence_items": evidence_items,
                "artifacts": {"read_mode": "windowed_by_hits" if matched_lines else "full_if_small" if len("\n".join(lines)) <= self.max_chars else "head_tail"},
                "facts": [],
            },
            references=[str(path)],
        )

    def _build_chunks(self, lines: list[str], matched_lines: list[int]) -> list[dict[str, Any]]:
        total_text = "\n".join(lines)
        if len("\n".join(lines)) <= self.max_chars:
            return [self._chunk(lines, 1, len(lines))]
        if len(lines) == 1:
            return [{"start_line": 1, "end_line": 1, "text": f"{total_text[: self.max_chars].rstrip()}\n...[已截断]"}]
        if matched_lines:
            center = matched_lines[0]
            start_line = max(1, center - self.window_radius)
            end_line = min(len(lines), center + self.window_radius)
            return [self._chunk(lines, start_line, end_line)]
        head_count = min(len(lines), max(1, self.window_radius))
        tail_start = max(1, len(lines) - self.window_radius + 1)
        chunks = [self._chunk(lines, 1, head_count)]
        if tail_start > head_count:
            chunks.append(self._chunk(lines, tail_start, len(lines)))
        return chunks

    def _chunk(self, lines: list[str], start_line: int, end_line: int) -> dict[str, Any]:
        text = "\n".join(lines[start_line - 1:end_line]).rstrip()
        return {"start_line": start_line, "end_line": end_line, "text": text}

    def _relative_path(self, path: Path, workspace_root: Path) -> str:
        try:
            return str(path.relative_to(workspace_root))
        except ValueError:
            return str(path)

    def _fallback_summary(self, path: Path, chunks: list[dict[str, Any]]) -> str:
        if not chunks:
            return f"No readable content found in {path.name}."
        return f"Read {path.name} lines {chunks[0]['start_line']}-{chunks[0]['end_line']}."
