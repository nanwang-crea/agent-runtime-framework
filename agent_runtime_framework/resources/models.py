from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class ResourceKind(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"
    DOCUMENT_CHUNK = "document_chunk"


@dataclass(frozen=True, slots=True)
class ResourceRef:
    resource_id: str
    kind: str
    location: str
    title: str

    @classmethod
    def for_path(cls, path: str | Path) -> "ResourceRef":
        resolved = Path(path).expanduser().resolve()
        kind = ResourceKind.DIRECTORY.value if resolved.is_dir() else ResourceKind.FILE.value
        return cls(
            resource_id=f"{kind}:{resolved}",
            kind=kind,
            location=str(resolved),
            title=resolved.name or str(resolved),
        )


@dataclass(slots=True)
class Resource:
    ref: ResourceRef
    title: str
    location: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FileResource(Resource):
    path: Path = field(default_factory=Path)
    size_bytes: int = 0
    mime_type: str = "text/plain"
    updated_at: datetime | None = None
    content_preview: str = ""


@dataclass(slots=True)
class DirectoryResource(Resource):
    path: Path = field(default_factory=Path)
    child_count: int = 0


@dataclass(slots=True)
class DocumentChunkResource(Resource):
    source_ref: ResourceRef = field(default_factory=lambda: ResourceRef("", "", "", ""))
    chunk_index: int = 0
    text: str = ""
