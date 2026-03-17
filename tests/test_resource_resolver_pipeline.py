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


def test_resolver_pipeline_prefers_direct_relative_path_over_recursive_name_search(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("root readme", encoding="utf-8")
    hidden_dir = workspace / ".pytest_cache"
    hidden_dir.mkdir()
    (hidden_dir / "README.md").write_text("cache readme", encoding="utf-8")
    repository = LocalFileResourceRepository([workspace])
    default_directory = ResourceRef.for_path(workspace)

    pipeline = ResolverPipeline.default()

    result = pipeline.resolve(
        ResolveRequest(
            user_input="读取 README.md",
            default_directory=default_directory,
        ),
        repository,
    )

    assert result == [ResourceRef.for_path(workspace / "README.md")]


def test_repository_find_by_name_prefers_non_hidden_and_direct_children(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    direct = workspace / "notes.md"
    direct.write_text("direct", encoding="utf-8")
    nested = workspace / "docs"
    nested.mkdir()
    (nested / "notes.md").write_text("nested", encoding="utf-8")
    hidden = workspace / ".cache"
    hidden.mkdir()
    (hidden / "notes.md").write_text("hidden", encoding="utf-8")
    repository = LocalFileResourceRepository([workspace])

    result = repository.find_by_name(ResourceRef.for_path(workspace), "notes.md")

    assert result[0] == ResourceRef.for_path(direct)
    assert result[1] == ResourceRef.for_path(nested / "notes.md")
    assert result[-1] == ResourceRef.for_path(hidden / "notes.md")
