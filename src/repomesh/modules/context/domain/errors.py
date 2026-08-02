from repomesh.shared.domain import DomainError


class ContextNotFound(DomainError):
    pass


class ContextAlreadyExists(DomainError):
    pass


class ContextConflict(DomainError):
    pass


class ContextSequenceConflict(ContextConflict):
    pass


class ContextPermissionDenied(DomainError):
    pass


class ContextAccessDenied(ContextPermissionDenied):
    pass


class ContextChangeRequestRequired(DomainError):
    pass
