class AgentDirectoryError(RuntimeError):
    pass


class AgentAlreadyExists(AgentDirectoryError):
    pass


class AgentHierarchyViolation(AgentDirectoryError):
    pass


class AgentPolicyViolation(AgentDirectoryError):
    pass
