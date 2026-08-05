import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from repomesh.shared.domain import new_id


def tokenize(text: str) -> frozenset[str]:
    return frozenset(token.lower() for token in re.findall(r"[\w-]+", text, re.UNICODE))


@dataclass(frozen=True, slots=True)
class AutoCard:
    """Compact repository snapshot used during repository discovery."""

    top_dirs: tuple[str, ...] = ()
    deps: tuple[str, ...] = ()
    recent_commits: tuple[str, ...] = ()
    exposed_apis: tuple[str, ...] = ()
    low_signal: bool = False


@dataclass(frozen=True, slots=True)
class RepositoryProfile:
    name: str
    url: str
    description: str = ""
    topics: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    auto_card: AutoCard | None = None
    id: UUID = field(default_factory=new_id)
    profiled_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def searchable_text(self) -> str:
        values = [self.name, self.description, *self.topics, *self.languages]
        if self.auto_card is not None:
            values.extend(self.auto_card.top_dirs)
            values.extend(self.auto_card.deps)
            values.extend(self.auto_card.recent_commits)
            values.extend(self.auto_card.exposed_apis)
        return " ".join(values)


@dataclass(frozen=True, slots=True)
class DiscoveryEvidence:
    repository_id: UUID
    matched_terms: tuple[str, ...]
    score: float
    rationale: str
    is_entry_point: bool = False
