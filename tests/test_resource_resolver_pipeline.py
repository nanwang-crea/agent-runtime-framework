from __future__ import annotations

from pathlib import Path

from agent_runtime_framework.resources import (
    LocalFileResourceRepository,
    ResolveRequest,
    ResourceRef,
    ResolverPipeline,
)


def test_resolver_pipeline_prefers_first_non_empty_strategy(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "note.md"
    file_path.write_text("hello", encoding="utf-8")
    repository = LocalFileResourceRepository([workspace])
    default_directory = ResourceRef.for_path(workspace)

    pipeline = ResolverPipeline(
        [
            lambda request, repository: [],
            lambda request, repository: [ResourceRef.for_path(file_path)],
        ]
    )

    result = pipeline.resolve(
        ResolveRequest(user_input="读取 note.md", default_directory=default_directory),
        repository,
    )

    assert result == [ResourceRef.for_path(file_path)]


def test_resolver_pipeline_resolves_last_focus_and_default_directory(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    focused = workspace / "focus.md"
    focused.write_text("hello", encoding="utf-8")
    repository = LocalFileResourceRepository([workspace])
    default_directory = ResourceRef.for_path(workspace)

    pipeline = ResolverPipeline.default()

    focused_result = pipeline.resolve(
        ResolveRequest(
            user_input="再看刚才那个文件",
            default_directory=default_directory,
            last_focused=[ResourceRef.for_path(focused)],
        ),
        repository,
    )
    directory_result = pipeline.resolve(
        ResolveRequest(
            user_input="列出当前目录",
            default_directory=default_directory,
        ),
        repository,
    )

    assert focused_result == [ResourceRef.for_path(focused)]
    assert directory_result == [default_directory]
