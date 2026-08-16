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
    test_commands: tuple[str, ...] = ()
    """How this repository verifies itself, in its own words (defect A-19).

    The integration LLM does not emit verification commands, so a plan's
    ``TaskNode.tests`` arrives empty and the console materialize path had
    nothing to put there — every console round dispatched ``testCommands: []``
    and the Runner dutifully ran nothing. The commands are a property of the
    repository, not of a requirement, so this is where they belong: one
    catalog row states once how its repository is checked, and every round
    over that repository inherits it.

    Empty is honest and stays legal: a repository nobody has told us how to
    test yields tasks with no verification, and delivery refuses the
    unverified candidate downstream rather than this field inventing a
    plausible ``pytest``.
    """
    test_paths: tuple[str, ...] = ()
    """Where this repository keeps the files its verification commands read.

    Defect A-21, and the other half of ``test_commands``: supplying the command
    without the path is a trap. A Worker's allowed paths come from its
    responsibility paths — ``src/checkout/**`` — while ``run_tests.py``
    discovers from the repository root's ``tests/``. So the compliant agent
    wrote the test where the command looks, and the path guard voided the
    entire run (``changed_path_denied: tests/test_discount.py``, commitSha
    null). The evading agent hid the test under ``src/`` where the command
    never finds it. Two agents, both dead ends, because the permission and the
    verification described different repositories.

    Added to a task's allowed paths, never substituted for them: a repository
    saying where its tests live cannot be a way to widen what a Worker may
    touch elsewhere.
    """
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
