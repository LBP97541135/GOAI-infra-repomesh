# Platform Configuration

Owns deployment-wide encrypted credentials and their persistence. Credentials are global to a
RepoMesh installation and are not scoped to an organization. API authorization remains owned by
`identity_access`; this module receives the authenticated account id as audit metadata.

It also owns the durable state machine for two-stage platform bootstrap. The domain contract knows
operation kinds, phases, leases, transitions, and safe error codes. Docker access, subprocesses,
and the AgentTeams installer remain infrastructure concerns of the isolated bootstrap service and
must not enter `bootstrap.py` or the normal RepoMesh API container.
