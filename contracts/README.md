# Cross-Process Contracts

This directory contains language-neutral contracts shared by independently deployed RepoMesh
product components. It is not a replacement for module-owned Python contracts under
`repomesh.modules.<module>.contracts`.

Every version directory is immutable after a replacement version is published. Contract changes
must include producer and consumer compatibility tests before either implementation relies on the
new shape.
