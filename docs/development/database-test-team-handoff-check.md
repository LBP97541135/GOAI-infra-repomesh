# Database Test Team Handoff Check

- [x] Manager declaration is the source of database scope.
- [x] Business Worker cannot modify the declaration.
- [x] Handoff plan is SHA-fenced; repeated Task + SHA yields the same plan shape.
- [x] Handoff plan carries required checks and affected tables.
- [x] Test Team plan contains only opaque references and no secrets.
- [x] Test Team evidence validator cannot widen tables or change Candidate SHA.
- [x] Missing checks produce a named rework reason.
- [x] Test evidence is fenced to Task + candidate SHA.
- [x] Plan is dispatched to the cross-repository Test Team when a test-team topology is present.
- [x] Branch validation starts automatically after complete Test Team evidence.
- [x] Handoff plan status can be queried through the API.
- [ ] Handoff status is rendered in the delivery console UI.
- [ ] Polar execution remains blocked-external until an authorized provider exists.
