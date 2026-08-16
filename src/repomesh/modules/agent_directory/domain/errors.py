# The shared error types live in contracts.py so consumers can catch them
# through the module's public contract; the domain re-exports them for its
# own use. (contracts importing domain would be circular — the domain package
# imports contracts.)
from repomesh.modules.agent_directory.contracts import (
    AgentDirectoryError as AgentDirectoryError,
)
from repomesh.modules.agent_directory.contracts import (
    AgentHierarchyViolation as AgentHierarchyViolation,
)


class AgentAlreadyExists(AgentDirectoryError):
    pass


class AgentPolicyViolation(AgentDirectoryError):
    pass
