from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_runtime_framework.workflow.llm_access import get_workspace_root
from typing import Any

from agent_runtime_framework.workflow.runtime_protocols import RuntimeContextLike

from agent_runtime_framework.workflow.llm_synthesis import synthesize_text
from agent_runtime_framework.workflow.models import NODE_STATUS_COMPLETED, NodeResult, WorkflowNode, WorkflowRun

_COMMON_CODE_DIRECTORIES = ("src", "app", "agent_runtime_framework", "tests", "docs", "frontend-shell")
_ENTRYLIKE_FILES = {"README.md", "pyproject.toml", "package.json", "Makefile"}


@dataclass(slots=True)
class WorkspaceDiscoveryExecutor:
    max_root_entries: int = 200
    max_children_per_directory: int = 20

    def execute(self, node: WorkflowNode, run: WorkflowRun, context: RuntimeContextLike = None) -> NodeResult:
        runtime_context = dict(context or {})
        workspace_root = Path(get_workspace_root(runtime_context, ".")).resolve()
        tree_sample: list[str] = []
        evidence_items: list[dict[str, Any]] = []
        facts: list[dict[str, Any]] = []
        references: list[str] = []
        goal_terms = {term.lower() for term in str(run.goal or "").replace("/", " ").replace("_", " ").split() if term.strip()}

        root_entries = sorted(workspace_root.iterdir(), key=lambda item: item.name)[: self.max_root_entries]
        for path in root_entries:
            relative_path = self._relative_path(path, workspace_root)
            if relative_path not in tree_sample:
                tree_sample.append(relative_path + ("/" if path.is_dir() else ""))
            references.append(str(path))
            evidence_items.append(
                {
                    "kind": "directory" if path.is_dir() else "path",
                    "path": str(path),
                    "relative_path": relative_path,
                    "summary": f"Root entry: {relative_path}",
                    "score": self._score_path(path, goal_terms),
                }
            )
            fact = self._classify_fact(path, workspace_root)
            if fact is not None and fact not in facts:
                facts.append(fact)

        for directory_name in _COMMON_CODE_DIRECTORIES:
            directory = workspace_root / directory_name
            if not directory.exists() or not directory.is_dir():
                continue
            for child in sorted(directory.iterdir(), key=lambda item: item.name)[: self.max_children_per_directory]:
                relative_path = self._relative_path(child, workspace_root)
                tree_label = relative_path + ("/" if child.is_dir() else "")
                if tree_label not in tree_sample:
                    tree_sample.append(tree_label)
                references.append(str(child))
                evidence_items.append(
                    {
                        "kind": "directory" if child.is_dir() else "path",
                        "path": str(child),
                        "relative_path": relative_path,
                        "summary": f"Candidate under {directory_name}: {relative_path}",
                        "score": self._score_path(child, goal_terms),
                    }
                )
                fact = self._classify_fact(child, workspace_root)
                if fact is not None and fact not in facts:
                    facts.append(fact)

        references = list(dict.fromkeys(references))
        summary = synthesize_text(
            context,
            role="composer",
            system_prompt=(
                "You summarize discovered workspace structure for an end user. "
                "Mention the most relevant source roots, docs, tests, and entry/config files in one concise paragraph."
            ),
            payload={
                "goal": run.goal,
                "workspace_root": str(workspace_root),
                "facts": facts,
                "tree_sample": tree_sample[:20],
            },
            max_tokens=220,
        )
        if summary is None:
            raise RuntimeError("composer model unavailable for workspace_discovery summary")
        return NodeResult(
            status=NODE_STATUS_COMPLETED,
            output={
                "summary": summary,
                "facts": facts,
                "evidence_items": evidence_items,
                "artifacts": {"tree_sample": tree_sample},
            },
            references=references,
        )

    def _relative_path(self, path: Path, workspace_root: Path) -> str:
        try:
            return str(path.relative_to(workspace_root))
        except ValueError:
            return str(path)

    def _classify_fact(self, path: Path, workspace_root: Path) -> dict[str, Any] | None:
        relative_path = self._relative_path(path, workspace_root)
        if path.is_dir() and path.name in {"src", "app", "agent_runtime_framework", "frontend-shell"}:
            return {"kind": "source_root", "path": relative_path}
        if path.is_dir() and path.name == "tests":
            return {"kind": "test_root", "path": relative_path}
        if path.name in _ENTRYLIKE_FILES:
            return {"kind": "config_or_entry", "path": relative_path}
        return None

    def _score_path(self, path: Path, goal_terms: set[str]) -> float:
        haystack = str(path).lower()
        return float(sum(1 for term in goal_terms if term in haystack))
