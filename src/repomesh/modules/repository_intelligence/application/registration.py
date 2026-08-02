from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.modules.repository_intelligence.ports import RepositoryCatalog
from repomesh.shared.domain import new_id
from repomesh.shared.events import ActorType, EventEnvelope


class RegisterRepository:
    def __init__(self, catalog: RepositoryCatalog) -> None:
        self._catalog = catalog

    async def execute(
        self,
        profile: RepositoryProfile,
        *,
        actor_type: ActorType = ActorType.SERVICE,
        actor_id: str = "repomesh-api",
    ) -> None:
        event = EventEnvelope(
            event_type="RepositoryRegistered",
            actor_type=actor_type,
            actor_id=actor_id,
            aggregate_type="Repository",
            aggregate_id=profile.id,
            aggregate_version=1,
            correlation_id=new_id(),
            payload={"name": profile.name, "url": profile.url},
        )
        await self._catalog.add(profile, events=(event,))
