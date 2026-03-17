from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Protocol

from agent_runtime_framework.resources.models import (
    DirectoryResource,
    DocumentChunkResource,
    FileResource,
    Resource,
    ResourceKind,
    ResourceRef,
)


class ResourceRepository(Protocol):
    def get(self, ref: ResourceRef) -> Resource: ...

    def list_directory(self, ref: ResourceRef) -> list[ResourceRef]: ...

    def find_by_name(self, directory_ref: ResourceRef, name: str) -> list[ResourceRef]: ...

    def load_text(self, ref: ResourceRef) -> str: ...

    def load_document_chunks(self, ref: ResourceRef, *, chunk_size: int = 200) -> list[DocumentChunkResource]: ...


@dataclass(slots=True)
class LocalFileResourceRepository:
    allowed_roots: list[Path]

    def __init__(self, allowed_roots: Iterable[str | Path]) -> None:
        self.allowed_roots = [Path(root).expanduser().resolve() for root in allowed_roots]

    def _resolve_path(self, ref: ResourceRef) -> Path:
        path = Path(ref.location).expanduser().resolve()
        if not any(path == root or root in path.parents for root in self.allowed_roots):
            raise ValueError(f"path is outside allowed roots: {path}")
        if not path.exists():
            raise FileNotFoundError(path)
        return path

    def get(self, ref: ResourceRef) -> Resource:
        path = self._resolve_path(ref)
        if path.is_dir():
            children = list(path.iterdir())
            return DirectoryResource(
                ref=ResourceRef.for_path(path),
                title=path.name or str(path),
                location=str(path),
                path=path,
                child_count=len(children),
                metadata={"kind": ResourceKind.DIRECTORY.value},
            )
        stat = path.stat()
        preview = ""
        try:
            preview = path.read_text(encoding="utf-8")[:500]
        except Exception:
            preview = ""
        return FileResource(
            ref=ResourceRef.for_path(path),
            title=path.name,
            location=str(path),
            path=path,
            size_bytes=stat.st_size,
            updated_at=datetime.fromtimestamp(stat.st_mtime),
            content_preview=preview,
            metadata={"kind": ResourceKind.FILE.value},
        )

    def list_directory(self, ref: ResourceRef) -> list[ResourceRef]:
        path = self._resolve_path(ref)
        if not path.is_dir():
            raise NotADirectoryError(path)
        return [ResourceRef.for_path(child) for child in sorted(path.iterdir(), key=lambda item: item.name)]

    def find_by_name(self, directory_ref: ResourceRef, name: str) -> list[ResourceRef]:
        path = self._resolve_path(directory_ref)
        if not path.is_dir():
            raise NotADirectoryError(path)
        lowered = name.lower()
        matches: list[tuple[tuple[int, int, int, str], ResourceRef]] = []
        for child in path.rglob("*"):
            if lowered in child.name.lower():
                ref = ResourceRef.for_path(child)
                relative = child.relative_to(path)
                depth = len(relative.parts)
                hidden_penalty = 1 if any(part.startswith(".") for part in relative.parts) else 0
                exact_penalty = 0 if child.name.lower() == lowered else 1
                rank = (hidden_penalty, depth, exact_penalty, str(relative))
                matches.append((rank, ref))
        matches.sort(key=lambda item: item[0])
        return [ref for _, ref in matches]

    def load_text(self, ref: ResourceRef) -> str:
        path = self._resolve_path(ref)
        if path.is_dir():
            raise IsADirectoryError(path)
        return path.read_text(encoding="utf-8")

    def load_document_chunks(self, ref: ResourceRef, *, chunk_size: int = 200) -> list[DocumentChunkResource]:
        text = self.load_text(ref)
        words = text.split()
        if not words:
            return []
        chunks: list[DocumentChunkResource] = []
        for index, start in enumerate(range(0, len(words), chunk_size)):
            chunk_words = words[start : start + chunk_size]
            chunk_text = " ".join(chunk_words)
            chunks.append(
                DocumentChunkResource(
                    ref=ResourceRef(
                        resource_id=f"{ref.resource_id}#chunk:{index}",
                        kind=ResourceKind.DOCUMENT_CHUNK.value,
                        location=f"{ref.location}#chunk:{index}",
                        title=f"{ref.title} chunk {index}",
                    ),
                    title=f"{ref.title} chunk {index}",
                    location=f"{ref.location}#chunk:{index}",
                    source_ref=ref,
                    chunk_index=index,
                    text=chunk_text,
                    metadata={"kind": ResourceKind.DOCUMENT_CHUNK.value},
                )
            )
        return chunks
