# Architecture and request flows

This repository is a Django REST Framework modular monolith. It exposes an API and Swagger UI; there is no frontend, message queue, or background worker. Notifications are database rows written in the same transaction as the event or registration change that caused them.

## End-to-end product flow

This is the user and business process from account creation through event attendance. The component diagram below it is the separate system-design view.

```mermaid
flowchart TD
    Start([Start]) --> Account[Register an account or log in]
    Account --> Role{Which role is using the API?}

    Role -- Organizer --> OrganizerAction[Create, edit, or cancel an event]
    OrganizerAction --> EventValid{Event data and state valid?}
    EventValid -- No --> EventError[Return a JSON validation or state error]
    EventValid -- Yes --> EventSaved[Save event change]
    EventSaved --> EventCancelled{Was the event cancelled?}
    EventCancelled -- Yes --> CloseRegistrations[Close open registrations and notify attendees]
    EventCancelled -- No --> EventReady[Event is published or updated]
    CloseRegistrations --> End([Done])
    EventReady --> End

    Role -- Attendee --> Browse[Browse and filter upcoming events]
    Browse --> Select[Choose an event]
    Select --> Register[Submit registration]
    Register --> Eligible{Event open, before deadline,<br/>and no existing open registration?}
    Eligible -- No --> RegistrationError[Return a JSON state error]
    Eligible -- Yes --> Seat{Is a seat available?}
    Seat -- Yes --> Confirmed[Create registered entry]
    Seat -- No --> Waiting[Create waitlisted entry]
    Confirmed --> AttendeeNotice[Save confirmation notification]
    Waiting --> AttendeeNotice
    AttendeeNotice --> AttendOrCancel{Attendee cancels before event starts?}
    AttendOrCancel -- No --> End
    AttendOrCancel -- Yes --> ReleaseSeat[Cancel entry and release its seat if registered]
    ReleaseSeat --> PlaceAvailable{Seat available and waitlist non-empty?}
    PlaceAvailable -- No --> End
    PlaceAvailable -- Yes --> Promote[Promote oldest waitlisted attendee]
    Promote --> PromotionNotice[Save promotion notification]
    PromotionNotice --> PlaceAvailable

    EventError --> End
    RegistrationError --> End
```

## System architecture

```mermaid
flowchart LR
    Client[API clients]
    Swagger[Swagger UI]
    WSGI[WSGI entrypoint]
    ASGI[ASGI entrypoint]
    Middleware[Request middleware<br/>request id, metrics, body-size limit and JSON exception handling]
    Metrics[Prometheus metrics route<br/>off unless enabled]
    Logs[JSON logs<br/>access line per request]
    Router[Django URL router]

    subgraph API[REST API]
        Accounts[accounts<br/>registration, JWT login and refresh,<br/>current user, login lockout, rate limits]
        Events[events<br/>event create/read/edit/cancel,<br/>search and filtering]
        Registrations[registrations<br/>attendee registration, cancellation,<br/>waitlist, organizer lists, notifications]
        Shared[config<br/>settings, validation, permissions support,<br/>idempotency, rate limits, logging, health and error responses]
    end

    Admin[Configurable Django admin]
    Schema[OpenAPI schema and Swagger UI]
    Services[Transactional domain services<br/>seat locking, promotion, cancellation,<br/>notification creation]
    DB[(PostgreSQL)]

    Client --> WSGI
    Client --> ASGI
    Swagger --> Schema
    WSGI --> Middleware
    ASGI --> Middleware
    Middleware --> Router
    Router --> Accounts
    Router --> Events
    Router --> Registrations
    Router --> Admin
    Router --> Schema
    Router --> Metrics
    Middleware --> Logs
    Router --> Shared
    Accounts --> Shared
    Events --> Shared
    Registrations --> Shared
    Events --> Services
    Registrations --> Services
    Accounts --> DB
    Events --> DB
    Registrations --> DB
    Services --> DB
    Admin --> DB
    Shared --> DB
```

### Package responsibilities

| Area | Responsibility |
|---|---|
| `config/` | Django settings and WSGI/ASGI setup; root routes; request-id, metrics and body-size middleware; JSON error conversion; JSON log setup; the metrics route; the scoped rate limit class; health check; shared pagination and schema helpers; reusable validators, fields, and idempotent-create handling. |
| `accounts/` | Custom user and role model; public registration; JWT login/refresh; current-user endpoint; organizer/attendee permissions; database-backed sign-in lockout. |
| `events/` | Event model and admin; event validation and filtering; public event reads; organizer-only create, edit, cancel, and owned-event/registration listings. |
| `registrations/` | Registration, notification, and idempotency-record models; attendee registration/list/cancel; organizer registration view; waitlist ranking and transactional seat/promotion workflows. The seat services take plain values and never see the HTTP request. |
| `*_tests*.py` and `tests*.py` | API, security, hashing, index, structure, logging, metrics, rate limit, resilience, concurrency/idempotency, query-count, and documentation behavior tests across the apps. |
| `race_test.py`, `smoke_test.py` | Scripts that run against a live server: simultaneous requests for the last seat, and a check of every endpoint, the request id header and the metrics route. |
| `pyproject.toml`, `requirements-dev.txt` | Settings and packages for Ruff, mypy and coverage. |
| `events/management/commands/seed_demo.py` | Repeatable demo-data command. |

`manage.py` is the development and administrative command entrypoint. `config.wsgi` and `config.asgi` are the server entrypoints. Runtime settings come from environment variables; PostgreSQL is the configured database. The admin path is configurable, while the API schema and Swagger UI are public. `/metrics` always exists but answers the JSON `not_found` unless `METRICS_ENABLED` is true.

## Main data relationships

```mermaid
erDiagram
    USER ||--o{ EVENT : organizes
    USER ||--o{ REGISTRATION : attends
    EVENT ||--o{ REGISTRATION : accepts
    USER ||--o{ NOTIFICATION : receives
    EVENT ||--o{ NOTIFICATION : concerns
    REGISTRATION o|--o{ NOTIFICATION : concerns
    USER ||--o{ IDEMPOTENCY_RECORD : owns
    LOGIN_FAILURE }o..o{ USER : "username and IP, not a foreign key"
```

Open registrations (`registered` or `waitlisted`) are unique per attendee and event at the database level. Idempotency records are unique per user, endpoint, and key. `LoginFailure` is stored by normalized username and IP address rather than by user foreign key.

## Attendee registration and waitlist flow

```mermaid
flowchart TD
    Start[POST /api/registrations/] --> Size{Body within 1 MiB?}
    Size -- No --> TooLarge[413 payload_too_large]
    Size -- Yes --> Auth{Valid JWT?}
    Auth -- No --> Unauthorized[401 not_authenticated]
    Auth -- Yes --> Role{Attendee role?}
    Role -- No --> Forbidden[403 permission_denied]
    Role -- Yes --> Limit{Within the write rate limit?}
    Limit -- No --> TooMany[429 too_many_requests]
    Limit -- Yes --> Key{Idempotency-Key supplied?}

    Key -- Yes --> KeyValid{Key valid and not reused with another body?}
    KeyValid -- No --> KeyError[Validation error or 422 key-reused error]
    KeyValid -- Yes --> Replay{Matching saved success exists?}
    Replay -- Yes --> ReplayResponse[Replay stored response]
    Replay -- No --> Validate
    Key -- No --> Validate[Validate request and event]

    Validate --> Valid{Input valid?}
    Valid -- No --> Invalid[400 validation_error]
    Valid -- Yes --> Lock[Begin transaction and lock event row]
    Lock --> Eligible{Scheduled, not started,<br/>before deadline, no open registration?}
    Eligible -- No --> StateError[Return state error; roll back]
    Eligible -- Yes --> Seat{Registered count below capacity?}
    Seat -- Yes --> Confirmed[Create registered row]
    Seat -- No --> Waitlisted[Create waitlisted row]
    Confirmed --> Notify[Create matching notification]
    Waitlisted --> Notify
    Notify --> SaveKey[Save successful response if idempotency key was used]
    SaveKey --> Commit[Commit transaction]
    Commit --> Result[Return registration and waitlist position]
```

## Seat release, event changes, and promotion

```mermaid
flowchart TD
    Cancel[Attendee cancels registration] --> Lock[Lock event row in a transaction]
    Raise[Organizer raises capacity] --> Lock
    Lock --> IsCancel{Cancellation request?}
    IsCancel -- Yes --> Mark[Mark registration cancelled]
    Mark --> Held{Did it hold a seat?}
    Held -- No --> Finish[Commit]
    Held -- Yes --> CapacityCheck
    IsCancel -- No --> CapacityCheck{Registered count below capacity?}
    CapacityCheck -- No --> Finish[Commit]
    CapacityCheck -- Yes --> Oldest[Select oldest waitlisted registration]
    Oldest --> Exists{Waitlisted attendee found?}
    Exists -- No --> Finish
    Exists -- Yes --> Promote[Mark registered and create promoted notification]
    Promote --> CapacityCheck

    EventCancel[Organizer cancels event] --> EventLock[Lock event row in a transaction]
    EventLock --> Close[Mark event cancelled and close all open registrations]
    Close --> NotifyAll[Create event-cancelled notification for each attendee]
    NotifyAll --> CancelCommit[Commit]
```

Event edits that change a time, venue, format, or location notify registered and waitlisted attendees. Raising capacity uses the same event lock and promotion logic. Promotion may continue after the registration deadline, but not after the event starts.

## Request-wide behavior

1. The request-id middleware runs first. It keeps a valid `X-Request-ID` from the client or makes a new one, binds it to every log line of the request, and adds it to the answer, including errors and the 413.
2. The metrics middleware times the request and records a counter and a histogram under the matched route, or `unmatched`. It skips `/metrics` itself.
3. The body-size middleware rejects oversized declared request bodies before authentication.
4. Django routes the request to the relevant API, admin, health, schema, metrics or documentation view.
5. DRF applies JWT authentication and endpoint-specific role/ownership checks, and only then the rate limit for that endpoint, so a 401 or 403 is answered before anything is counted. A passed limit answers 429. The public API returns JSON errors through the shared exception handler.
6. Create endpoints may wrap the business operation in database-backed idempotency handling. Repeating the same key and body replays the first successful response; a different body is rejected.
7. Registration and event lifecycle services use database transactions and an event-row lock to protect seat counts and keep notifications consistent with state changes.
8. After the answer, one `api.access` line is written with the request id, method, path, status, duration and user id. Log lines are JSON, and never hold query strings, bodies, tokens or passwords.

See [README.md](../README.md) for endpoint details, request fields, and examples, and [DECISIONS.md](./DECISIONS.md) for the design rationale and trade-offs.