from .domain import DiscoveryEvidence, tokenize
from .ports import RepositoryCatalog


class RepositoryDiscoveryService:
    def __init__(self, catalog: RepositoryCatalog) -> None:
        self._catalog = catalog

    async def discover(self, requirement: str, limit: int = 5) -> list[DiscoveryEvidence]:
        query_terms = tokenize(requirement)
        ranked: list[DiscoveryEvidence] = []
        for profile in await self._catalog.list():
            matches = sorted(query_terms & tokenize(profile.searchable_text))
            if not matches:
                continue
            score = len(matches) / max(len(query_terms), 1)
            ranked.append(
                DiscoveryEvidence(
                    repository_id=profile.id,
                    matched_terms=tuple(matches),
                    score=round(score, 4),
                    rationale=f"Matched {len(matches)} requirement term(s): {', '.join(matches)}",
                )
            )
        return sorted(ranked, key=lambda item: (-item.score, str(item.repository_id)))[:limit]

