import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from repomesh.shared.domain import new_id


def tokenize(text: str) -> frozenset[str]:
    return frozenset(token.lower() for token in re.findall(r"[\w-]+", text, re.UNICODE))


@dataclass(frozen=True, slots=True)
class RepositoryProfile:
    name: str
    url: str
    description: str
    topics: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    id: UUID = field(default_factory=new_id)
    profiled_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def searchable_text(self) -> str:
        return " ".join((self.name, self.description, *self.topics, *self.languages))


@dataclass(frozen=True, slots=True)
class DiscoveryEvidence:
    repository_id: UUID
    matched_terms: tuple[str, ...]
    score: float
    rationale: str
