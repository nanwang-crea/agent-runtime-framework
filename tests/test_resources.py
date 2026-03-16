from __future__ import annotations

from pathlib import Path

from agent_runtime_framework.resources import (
    DirectoryResource,
    DocumentChunkResource,
    FileResource,
    InMemoryResourceIndex,
    LocalFileResourceRepository,
    ResourceRef,
)


def test_repository_builds_file_and_directory_resources(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "note.md"
    file_path.write_text("hello world", encoding="utf-8")

    repository = LocalFileResourceRepository([workspace])

    directory = repository.get(ResourceRef.for_path(workspace))
    file_resource = repository.get(ResourceRef.for_path(file_path))

    assert isinstance(directory, DirectoryResource)
    assert isinstance(file_resource, FileResource)
    assert file_resource.path == file_path
    assert file_resource.content_preview == "hello world"


def test_repository_splits_document_into_chunks(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "story.txt"
    file_path.write_text("alpha beta gamma delta epsilon zeta", encoding="utf-8")

    repository = LocalFileResourceRepository([workspace])

    chunks = repository.load_document_chunks(
        ResourceRef.for_path(file_path),
        chunk_size=3,
    )

    assert chunks
    assert isinstance(chunks[0], DocumentChunkResource)
    assert chunks[0].text == "alpha beta gamma"


def test_in_memory_resource_index_round_trips_resources(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "note.md"
    file_path.write_text("summary text", encoding="utf-8")

    repository = LocalFileResourceRepository([workspace])
    resource = repository.get(ResourceRef.for_path(file_path))

    index = InMemoryResourceIndex()
    index.put(resource)

    assert index.get(resource.ref.resource_id) == resource
