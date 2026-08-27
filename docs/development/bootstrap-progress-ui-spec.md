# Bootstrap Progress UI Implementation Spec

Status: implemented and accepted for B7

## Outcome

After model credentials are saved, the setup wizard immediately shows durable execution-plane
bootstrap progress. Refreshing or reopening the page restores the current operation. The user never
sees a command, token, raw installer output, or internal container detail.

## API Client

Add typed session-authenticated calls:

- `fetchBootstrapStatus()` -> `GET /api/v1/setup/bootstrap`;
- `retryBootstrap()` -> `POST /api/v1/setup/bootstrap/retry`.

The local-account cookie channel is mandatory. The shared browser action token must not be used for
bootstrap control.

## States

| State | UI behavior |
| --- | --- |
| `idle` | No progress panel |
| `pending` | Automatic setup queued; poll |
| `running` | Show phase and attempt; poll |
| `waiting_for_user` | Stop polling; link to model step |
| `retryable_failure` | Stop polling; show safe detail and Retry button |
| `terminal_failure` | Stop polling; show safe detail without Retry |
| `completed` | Show ready state, refresh setup dependencies, enable console entry |

Polling interval is 1.5 seconds only for `pending` and `running`. A response schedules the next poll;
requests never overlap. Component unmount cancels the timer and ignores late responses.

## Phase Labels

- waiting for model;
- installing AgentTeams;
- verifying Controller;
- configuring Matrix;
- configuring object storage;
- writing runtime configuration;
- restarting API;
- verifying platform;
- complete.

The UI maps stable phase IDs to Chinese labels. Server `message` is fallback diagnostic text, not
the primary visible label.

## Model Save

Successful model save:

1. displays the existing save/restart receipt;
2. immediately fetches bootstrap status;
3. keeps the user on the model step;
4. renders progress without requiring a page reload;
5. does not infer completion from the credential save response.

## Retry

Retry is visible only when `retryable=true`. While the request is in flight the button is disabled.
Successful retry replaces local status with the returned pending operation and resumes polling.
409 is shown as a safe ordinary error and does not clear the previous status.

## Layout

The progress surface is an unframed section with one status rail, not nested cards. It appears above
the active step content and uses fixed row height constraints to prevent layout shift between phase
labels. It must fit desktop and mobile widths without horizontal scrolling.

## Tests

- TypeScript contract covers every state and phase;
- polling occurs only for pending/running and never overlaps;
- model save refreshes progress immediately;
- retry visibility and disabled state follow the response;
- completed progress refreshes setup status;
- Browser desktop/mobile checks show no overlap and no console errors.

## Done

B7 is complete when an API-driven synthetic operation can be observed through pending/running/
completed states in the browser, retry UI is behaviorally tested, and frontend lint/build pass.
