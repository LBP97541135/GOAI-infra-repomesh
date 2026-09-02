# Manager Database Task Automation Tasks

| ID | Status | Task |
|---|---|---|
| MDT-1 | complete | Define Manager database declaration and trigger decision contracts |
| MDT-2 | complete | Persist declaration on Task and expose it through TaskView |
| MDT-3 | complete | Carry declaration through leader plan and Bridge wire documents |
| MDT-4 | complete | Carry requirement in Worker assignment/spec package |
| MDT-5 | complete | Parse the controlled Worker database-change report into Runner evidence |
| MDT-6 | complete | Cross-check declaration, evidence, required checks, tables, and database diff paths |
| MDT-7 | complete | Automatically request database Branch validation once per candidate SHA |
| MDT-8 | blocked-external | Execute the request through a live Polar Branch provider |

MDT-8 remains blocked until an authorized Polar test environment is available.

The first diff detector covers migration/schema directories and SQL files. Repository-specific ORM
and query paths remain a follow-up configuration layer; until configured, the Manager declaration
is authoritative and the generic detector is the undeclared-change safety net.
