"""Local filesystem cache for organisation-wide AutoCard data.

Caches are stored per organisation/group under ``.repomesh_cache/{org_hash}/``.
Each repository's data is a JSON file containing the profile + AutoCard +
timestamp.  The cache reduces repeated API calls when the user issues
multiple queries against the same organisation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

from repomesh.modules.repository_intelligence.domain import (
    AutoCard,
    DepEvidence,
    RepositoryProfile,
)

_logger = logging.getLogger(__name__)

#: Default cache directory (relative to project root).
_DEFAULT_CACHE_DIR = Path(".repomesh_cache")

#: Cache entry version — bump when the on-disk format changes.
_CACHE_VERSION = 1


class OrgCache:
    """Manages a per-organisation cache directory on the local filesystem.

    Usage::

        cache = OrgCache()
        profiles = cache.load(group_url)
        if profiles is None:
            profiles = await scan_org(...)
            cache.save(group_url, profiles)
    """

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        self._base_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _org_hash(org_url: str) -> str:
        """Deterministic short hash for an org URL."""

        return hashlib.sha256(org_url.encode("utf-8")).hexdigest()[:16]

    def _org_dir(self, org_url: str) -> Path:
        return self._base_dir / self._org_hash(org_url)

    # ------------------------------------------------------------------ load

    def load(
        self,
        org_url: str,
        *,
        max_age_hours: int = 24,
    ) -> list[RepositoryProfile] | None:
        """Load cached profiles for *org_url*.

        Returns ``None`` if the cache does not exist or is older than
        *max_age_hours*.
        """

        org_dir = self._org_dir(org_url)
        meta_file = org_dir / "_meta.json"
        if not meta_file.is_file():
            return None

        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        # Check version.
        if meta.get("version") != _CACHE_VERSION:
            _logger.debug("Cache version mismatch, ignoring")
            return None

        # Check age.
        cached_at = meta.get("cached_at", 0)
        age_hours = (time.time() - cached_at) / 3600
        if age_hours > max_age_hours:
            _logger.debug("Cache expired (%.1fh > %dh)", age_hours, max_age_hours)
            return None

        # Load individual repo files.
        profiles: list[RepositoryProfile] = []
        for repo_file in sorted(org_dir.glob("*.json")):
            if repo_file.name == "_meta.json":
                continue
            try:
                data = json.loads(repo_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            profile = _deserialize_profile(data)
            if profile is not None:
                profiles.append(profile)

        if not profiles:
            return None
        return profiles

    # ------------------------------------------------------------------ save

    def save(self, org_url: str, profiles: list[RepositoryProfile]) -> None:
        """Write *profiles* to the cache for *org_url*."""

        org_dir = self._org_dir(org_url)
        org_dir.mkdir(parents=True, exist_ok=True)

        # Write each repo as a separate file.
        for profile in profiles:
            repo_file = org_dir / f"{_safe_filename(profile.name)}.json"
            repo_file.write_text(
                json.dumps(_serialize_profile(profile), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # Write meta.
        meta = {
            "version": _CACHE_VERSION,
            "org_url": org_url,
            "cached_at": time.time(),
            "repo_count": len(profiles),
        }
        (org_dir / "_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ clear

    def clear(self) -> None:
        """Remove the entire cache directory."""

        if self._base_dir.exists():
            import shutil  # noqa: PLC0415

            shutil.rmtree(self._base_dir)

    # ------------------------------------------------------------------ status

    def get_age_hours(self, org_url: str) -> float | None:
        """Return the age of the cache in hours, or ``None`` if no cache."""

        meta_file = self._org_dir(org_url) / "_meta.json"
        if not meta_file.is_file():
            return None
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        cached_at = meta.get("cached_at", 0)
        if not cached_at:
            return None
        return (time.time() - cached_at) / 3600

    def get_repo_count(self, org_url: str) -> int:
        """Return the number of cached repos for *org_url* (0 if no cache)."""

        org_dir = self._org_dir(org_url)
        if not org_dir.is_dir():
            return 0
        return len(list(org_dir.glob("*.json"))) - 1  # minus _meta.json


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _serialize_profile(profile: RepositoryProfile) -> dict:
    data: dict = {
        "name": profile.name,
        "url": profile.url,
        "description": profile.description,
        "topics": list(profile.topics),
        "languages": list(profile.languages),
    }
    if profile.auto_card is not None:
        card = profile.auto_card
        data["auto_card"] = {
            "top_dirs": list(card.top_dirs),
            "deps": list(card.deps),
            "dep_evidence": [
                {
                    "name": evidence.name,
                    "mechanism": evidence.mechanism,
                    "confidence": evidence.confidence,
                }
                for evidence in card.dep_evidence
            ],
            "identities": list(card.identities),
            "recent_commits": list(card.recent_commits),
            "exposed_apis": list(card.exposed_apis),
            "low_signal": card.low_signal,
        }
    return data


def _deserialize_profile(data: dict) -> RepositoryProfile | None:
    try:
        auto_card = None
        if "auto_card" in data:
            ac = data["auto_card"]
            auto_card = AutoCard(
                top_dirs=tuple(ac.get("top_dirs") or ()),
                deps=tuple(ac.get("deps") or ()),
                dep_evidence=tuple(
                    DepEvidence(
                        name=item["name"],
                        mechanism=item["mechanism"],
                        confidence=item["confidence"],
                    )
                    for item in ac.get("dep_evidence") or ()
                ),
                identities=tuple(ac.get("identities") or ()),
                recent_commits=tuple(ac.get("recent_commits") or ()),
                exposed_apis=tuple(ac.get("exposed_apis") or ()),
                low_signal=ac.get("low_signal", False),
            )
        return RepositoryProfile(
            name=data["name"],
            url=data["url"],
            description=data.get("description", ""),
            topics=tuple(data.get("topics") or ()),
            languages=tuple(data.get("languages") or ()),
            auto_card=auto_card,
        )
    except (KeyError, TypeError):
        return None


def _safe_filename(name: str) -> str:
    """Make a repo name safe for use as a filename."""

    import re  # noqa: PLC0415

    return re.sub(r'[^\w\-.]', '_', name)
