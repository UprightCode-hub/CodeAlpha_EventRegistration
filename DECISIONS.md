# Decisions

Each entry gives the decision, the alternative I rejected, why, and what breaks if it is changed.

## Accounts and errors

- **Decision:** Use a custom `accounts.User` with a required unique email and a role of `organizer` or `attendee`. The role defaults to `attendee`, and a superuser created with `createsuperuser` becomes an organizer.
- **Alternative rejected:** Django's default user model with no role.
- **Why:** Every permission depends on the role, and it is cheap to set up before the first migration and painful to add later.
- **What breaks if changed:** Role checks would need a second source of truth, and `createsuperuser` would ask for a role.

- **Decision:** Email is stored in lowercase and is unique ignoring case.
- **Alternative rejected:** Compare emails exactly as typed.
- **Why:** `Ada@x.com` and `ada@x.com` are the same person, and two accounts would split their registrations.
- **What breaks if changed:** One person could hold two accounts and two seats.

- **Decision:** The role is chosen at registration, and staff rights can never be set through the API.
- **Alternative rejected:** An approval stage before someone becomes an organizer.
- **Why:** Approval needs an admin workflow that is outside this project. The cost is that anyone can become an organizer, and I list it under known gaps.
- **What breaks if changed:** Registration would need a pending state and an approval endpoint.

- **Decision:** Login returns a refresh token, and a refresh endpoint consumes it.
- **Alternative rejected:** Return a refresh token that nothing uses.
- **Why:** A token that cannot be used is only confusing. Short access tokens are safer when they can be renewed.
- **What breaks if changed:** Users would have to sign in again every 30 minutes.

- **Decision:** Every error uses one JSON shape with a code and a plain sentence. Validation errors add a `fields` object. A wrong password and an unknown username give the same answer.
- **Alternative rejected:** Return serializer output or exception text, or say which of the two was wrong.
- **Why:** One shape is easy for a client to handle. Naming the field helps the client. The same answer for both login failures does not reveal which usernames exist.
- **What breaks if changed:** Clients would face several formats, and login could be used to find valid usernames.

- **Decision:** Unknown URLs return the JSON `not_found` even when `DEBUG=True`, and the admin path without its trailing slash redirects to the path with it. The not-found route is exempt from the CSRF check.
- **Alternative rejected:** Rely on Django's default 404 page.
- **Why:** With `DEBUG=True` the default page is HTML. A catch-all route fixes that, but it would swallow the admin redirect, so the redirect is explicit. Without the CSRF exemption, a POST, PUT or DELETE to an unknown URL got an HTML 403 over real HTTP, which the Django test client never showed because it skips the CSRF check.
- **What breaks if changed:** Local development would return HTML for unknown URLs, the admin path without a slash would return a 404, or unknown URLs would answer HTML for anything but GET.

- **Decision:** `ALLOWED_HOSTS` and `DEBUG` come from the environment, and there is no hardcoded secret key.
- **Alternative rejected:** Values written in `settings.py`.
- **Why:** The same code must run on a laptop and on a server with different settings.
- **What breaks if changed:** Deploying would need a code change.

- **Decision:** Failed sign-ins are limited. Five failures for the same username from the same address in 15 minutes, or twenty from one address under any username, give a 429 with a `Retry-After` header. A blocked attempt does not check the password and is not counted. A successful login clears that username and address.
- **Alternative rejected:** Lock by username alone, or leave sign-in unlimited.
- **Why:** A lock on the username alone lets anyone lock a victim out by guessing wrong on purpose. Adding the address makes the lock apply to the attacker. The cap per address stops one address from trying many usernames. An unknown username locks in the same way as a known one, so the lockout does not reveal which usernames exist.
- **What breaks if changed:** Sign-in could be guessed at without limit, or a stranger could keep a real user out of their account.

- **Decision:** Failed sign-ins are stored in a database table and not in a cache.
- **Alternative rejected:** Count failures in a cache.
- **Why:** Every server process reads the same database, so the count does not depend on which process answered. It needs no extra service, and rows written in a test roll back with the test. Rows older than the window are deleted when a new failure is recorded.
- **What breaks if changed:** The project would need a cache service, and counts could differ between processes.

- **Decision:** Failed and blocked sign-ins are written to the log at warning level, with the username shown through `%r` and the address. The password is never logged. A password over 128 characters is refused at register and counted as a failure at login, and a username over 150 characters is counted as a failure at login.
- **Alternative rejected:** Log nothing, or log the raw username.
- **Why:** The log shows an attack as it happens. `%r` keeps control characters in a username from forging a log line. Without a length cap, a very long password makes the hasher do far more work than a normal one.
- **What breaks if changed:** Attacks would leave no trace, and one request could cost the server a lot of hashing time.

## Events

- **Decision:** Events cannot be deleted through the API, and the database refuses to delete an event that has registrations.
- **Alternative rejected:** A delete endpoint, or a hidden "deleted" flag.
- **Why:** Registrations are records of people who planned to attend. Calling an event off is a status change with a reason, not a deletion.
- **What breaks if changed:** Deleting an event would erase its registrations or fail with a database error.

- **Decision:** An organizer can call an event off by giving a reason. In one transaction the event becomes `cancelled`, every registered and waitlisted registration becomes `event_cancelled`, and each attendee gets a notification.
- **Alternative rejected:** Make the changes one at a time, or leave attendees to find out themselves.
- **Why:** If only part of it were saved, attendees would hold seats at an event that no longer exists. `event_cancelled` is separate from `cancelled` so the history says who ended the registration.
- **What breaks if changed:** The event and its registrations could disagree, and attendees would not be told.

- **Decision:** The public list shows only scheduled events in the future. A cancelled event stays reachable by its id with its status and reason, and `status=cancelled` lists them.
- **Alternative rejected:** Show cancelled events in the main list, or return 404 for them.
- **Why:** Browsing stays useful, and someone with an old link or a notification sees what happened and not an error.
- **What breaks if changed:** The list would fill with called-off events, or old links would give no explanation.

- **Decision:** Times are stored in UTC and must include a timezone offset. A time without an offset is rejected.
- **Alternative rejected:** Accept times without an offset and assume a zone.
- **Why:** An event at 18:00 means different moments in Lagos and London. A guess would put events at the wrong time.
- **What breaks if changed:** Events could silently start an hour or more off.

- **Decision:** An event needs a start in the future when it is created, and it cannot be edited, registered for or called off once it has started. The registration deadline is on or before the start and defaults to the start.
- **Alternative rejected:** Allow changes after the start.
- **Why:** After the start, changing an event rewrites history and notifies people about something that already happened.
- **What breaks if changed:** Past events could be edited and attendees notified of changes that no longer matter.

- **Decision:** An organizer who does not own an event gets 404, and an attendee on an organizer endpoint gets 403.
- **Alternative rejected:** Return 403 for someone else's event.
- **Why:** A 403 confirms the event exists. A 404 reveals nothing.
- **What breaks if changed:** Anyone could probe which event ids belong to other organizers.

- **Decision:** Attendee phone numbers and emails are shown only to the organizer who owns the event. Phones use international format with a country code.
- **Alternative rejected:** Show contact details on public pages, or accept any text as a phone number.
- **Why:** An organizer needs to reach attendees, and nobody else should. A fixed format makes numbers usable.
- **What breaks if changed:** Contact details would be public, or organizers would get numbers they cannot dial.

- **Decision:** Every text field has a length limit that matches a database column: venue name 200, address 255, city 100, link 2000, contact email 254. A longer value is a 400 naming that field.
- **Alternative rejected:** Unbounded text fields.
- **Why:** With no limit, a very long value either fills the database or fails inside it and becomes a 500. A limit turns it into an answer the client can act on.
- **What breaks if changed:** Long input could cause server errors.

- **Decision:** On the list endpoints, a page that is not a number, is zero or negative, or is past the last page is a 404. A filter value that is not one of the choices, and a bad date, is a 400 that names the parameter. Unknown parameters and empty values are ignored, and the client cannot change the page size.
- **Alternative rejected:** Quietly fall back to page one, or ignore a bad filter value.
- **Why:** A client that asks for something that does not exist should be told, not given a different answer than it asked for. An ignored bad filter would look like a real result. Ignoring unknown parameters keeps harmless extras from breaking a request.
- **What breaks if changed:** A typo in a filter would return a full, unfiltered list and look like success.

- **Decision:** The date filters compare the UTC date of the start and include the given day. The value is not used in date arithmetic.
- **Alternative rejected:** Compare against the end of the day by adding one day to the value.
- **Why:** Adding a day to `9999-12-31` overflows. Comparing the date itself works for every valid date.
- **What breaks if changed:** The largest and smallest dates would cause errors.

## Registrations and seats

- **Decision:** Seats are protected by a lock on the event row inside a transaction. Registering, cancelling, promoting, changing capacity and calling the event off all take the same lock.
- **Alternative rejected:** Count registrations and then insert, with no lock.
- **Why:** If 49 of 50 seats are taken and two people register at the same moment, both can see one free seat and both pass the check. One lock makes the second request wait and then see the real count.
- **What breaks if changed:** A 50 seat event could end with 51 people.

- **Decision:** One open registration per attendee and event, enforced by a conditional unique constraint that applies only while the status is `registered` or `waitlisted`.
- **Alternative rejected:** Check in code only, or one unique row per attendee and event.
- **Why:** The database is the last guard if the code check is skipped or beaten. A cancelled row must not block the same person from registering again, and keeping the old row preserves the history.
- **What breaks if changed:** Duplicates could be stored, or a person who cancelled could never come back.

- **Decision:** Cancelling a registration changes its status and keeps the row. A new registration after cancelling is a new row.
- **Alternative rejected:** Delete the row on cancel.
- **Why:** A record that someone registered and cancelled is useful to the organizer and cannot be reconstructed after a delete.
- **What breaks if changed:** The history of who held and released a seat would be lost.

- **Decision:** When an event is full, a new registration becomes `waitlisted`, oldest first. When a seat is freed or capacity is raised, the oldest waitlisted people are promoted automatically and notified, in the same transaction as the change.
- **Alternative rejected:** Refuse registrations when full, or give a promoted person a confirmation window.
- **Why:** Cancellations are common and the waitlist keeps those seats from going empty. A confirmation window needs timers and a second race, which is more than this project needs.
- **What breaks if changed:** Freed seats would stay empty, or promotion would need a background job.

- **Decision:** The registration deadline stops new registrations and new waitlist entries. Cancelling and promotion continue until the event starts.
- **Alternative rejected:** Freeze everything at the deadline.
- **Why:** A seat that frees up after the deadline should still go to the next person on the list.
- **What breaks if changed:** Late cancellations would waste seats.

- **Decision:** One seat per registration, with no guests.
- **Alternative rejected:** A guest count on each registration.
- **Why:** Guests would make seat counting and promotion harder, because a registration of three might not fit in one free seat.
- **What breaks if changed:** The seat count would need a sum and the waitlist would need fitting rules.

- **Decision:** Editing an event takes the same lock as registering, and checks the saved state under it. Order of checks: signed in, organizer, owner, then under the lock `event_cancelled`, `event_started`, field validation, and last `capacity_too_low`. If anything fails, nothing is saved.
- **Alternative rejected:** Validate first and take the lock only to save, or report field errors before state errors.
- **Why:** If the state were read before the lock, a registration or a cancel could land in between and the edit would compare against stale values. Telling a client that the event is already over is more useful than listing typos in a request that could never succeed. Field validation runs before `capacity_too_low` so that a capacity of 0 is reported as invalid and not as too low.
- **What breaks if changed:** An edit could promote or reject people on out-of-date counts, and a request to a dead event could get a misleading answer.

- **Decision:** Lists compute the seat counts in the same query as the events, and the registration and notification lists compute the waitlist position in the query. A page of 10 costs the same number of queries as a page of 2.
- **Alternative rejected:** Count per row in the serializer.
- **Why:** A count per row turns one list into eleven queries or more. Tests compare the query count for pages of different sizes, so a regression shows.
- **What breaks if changed:** List pages would get slower as pages fill, and the cost would grow with the data.

## Notifications

- **Decision:** Notifications are rows written in the same transaction as the change that causes them. There is no message queue.
- **Alternative rejected:** Send them from a background worker with a queue.
- **Why:** The race that matters is two requests for the same seat, and a queue does not fix it. The row lock does. A notification here is one row in the database, so a worker would add another service and a new failure mode, where the change is saved and the message is lost. A queue pays off when slow work such as email delivery arrives.
- **What breaks if changed:** The project would need Redis, a worker and a way to retry lost messages.

- **Decision:** Attendees are notified when an event's time, format or place changes, and when they are promoted from the waitlist.
- **Alternative rejected:** Let attendees find changes on their own.
- **Why:** A change of time or place is something an attendee must know about.
- **What breaks if changed:** People could turn up at the old time or place.

- **Decision:** An `event_updated` notification is sent only when a start, end, event type, venue, address, city or link actually changed value. Times are compared as instants, so the same moment written with another offset is not a change. Only registered and waitlisted attendees are told.
- **Alternative rejected:** Notify on every edit, or compare the text as sent.
- **Why:** A notification that arrives when nothing changed teaches people to ignore them. A time sent as `+01:00` and the same time stored as UTC are the same moment.
- **What breaks if changed:** Attendees would get messages about edits that do not affect them, or about no change at all.

- **Decision:** Notification messages are cut to 255 characters.
- **Alternative rejected:** Let the database reject a long event title.
- **Why:** An attendee should not be blocked because of how long an organizer wrote a title.
- **What breaks if changed:** A long title could cause a server error when someone registers.

## Retries and failures

- **Decision:** Creating an event and creating a registration accept an optional `Idempotency-Key` header. The first request with a key runs normally and its answer is stored with the key in the same transaction as the change. The same key and body within 24 hours replays the stored answer with the header `Idempotent-Replayed: true` and runs nothing. The same key with a different body is a 422. A failed answer is never stored and everything it wrote is rolled back. Two requests with the same key at the same moment run the change once.
- **Alternative rejected:** A cache or a queue to remember keys, or no keys at all.
- **Why:** A client that loses the answer to a registration cannot tell whether it worked, and a blind retry would be refused or double the change. Storing the key in the database, in the same transaction, means the key and the change are saved together or not at all. The unique constraint on user, endpoint and key is what makes two simultaneous requests run the change once, because the second insert waits for the first. A cache would add a service and could lose a key that the database still had a change for. Cancels and edits are already safe to repeat, so they do not take a key.
- **What breaks if changed:** A retried request could create a second event, or the stored key and the change could disagree after a crash.

- **Decision:** Requests with a body over 1 MB are refused with a 413 before authentication, by a middleware that reads the `Content-Length` header.
- **Alternative rejected:** No limit, or a limit in the web server only.
- **Why:** Django does not enforce a size limit on the JSON bodies the API parses, and the development server has no limit of its own. A middleware refuses the request before any other work is done. It reads only the declared length, so a request that sends no length is not caught here.
- **What breaks if changed:** One request could send a very large body and use the server's memory.

- **Decision:** A database failure answers 503 `service_unavailable` with `Retry-After: 5` and no exception text. The traceback goes to the server log at error level. Every other unhandled error stays the generic 500. The database connection times out after 5 seconds.
- **Alternative rejected:** Let a database failure become the generic 500, or leave the connection to wait without a limit.
- **Why:** A 500 says the code is broken. A 503 says to try again, which is true when the database is down or restarting. The 5 second timeout lets a dead database fail fast and not hold a request open. Checked by stopping the PostgreSQL service: the API answered 503 and recovered when the service started, without restarting the server.
- **What breaks if changed:** Clients would treat a short outage as a bug, and requests could hang.

- **Decision:** `GET /api/health/` is public, uses no authentication, runs one trivial query, and answers 200 `{"status": "ok"}`. When the database fails it answers 503. It is not in the schema.
- **Alternative rejected:** No health endpoint, or one that returns 200 whatever the database does.
- **Why:** Anything that watches the service needs one cheap URL that says whether the database is reachable. A health check that cannot fail is no check.
- **What breaks if changed:** There would be no simple way to tell whether the API can reach its database.

## Admin, demo data and docs

- **Decision:** Every model is registered in the Django admin with useful columns, filters and search fields. Lists use `select_related` and annotated counts, so a list page costs the same number of queries for 2 rows as for 12. Notifications cannot be added in the admin. `LoginFailure` and `IdempotencyRecord` are read only.
- **Alternative rejected:** Default admin pages, or counts taken from the model properties row by row.
- **Why:** The admin is how the data is inspected by hand. Notifications are written by the system inside the seat transaction, and a hand-made one would not match a real change. The sign-in and key records are evidence and should not be edited.
- **What breaks if changed:** Admin pages would slow down as data grows, and people could add notifications that no change caused.

- **Decision:** `seed_demo` creates `demo_organizer` and `demo_attendee` only when they do not exist, never resets a password, and creates three future events found by organizer and title. Running it twice creates nothing the second time.
- **Alternative rejected:** Reset the demo data on every run.
- **Why:** It is safe to run at any time and does not overwrite what someone changed.
- **What breaks if changed:** A second run could duplicate events or change a password someone had set.

- **Decision:** The Django admin is served at a path read from `ADMIN_URL` in `.env`, with the default `eventreg-manage/`, and not at `/admin/`. A value that is empty, has more than one segment, has a character other than a letter, digit, hyphen or underscore, or is `admin` in any letter case stops the project from starting. `/admin/` and everything under it answer the JSON `not_found`, like any unknown URL.
- **Alternative rejected:** Keep the admin at `/admin/`, or remove it.
- **Why:** Scans try `/admin/` first, so a different path keeps the admin out of those scans. It is an extra layer and not the protection. Anyone who learns the path sees the same sign in page, and the default value is public in the repository, so a real deployment sets its own. The protection is a strong superuser password, no weak staff accounts, HTTPS and a sign in lockout, which the admin sign in does not have yet. A bad value stops the project at startup, because serving the admin at a path nobody meant is worse than not starting.
- **What breaks if changed:** The admin would sit at the most scanned path, or a typo in `.env` would start the project with the admin somewhere unexpected.

- **Decision:** The API schema and the Swagger page at `/api/schema/` and `/api/docs/` are public. Every operation is described with `@extend_schema`, because the views are plain `APIView`s and the schema cannot find their shapes by itself.
- **Alternative rejected:** Generic views or viewsets built only so the schema generates itself.
- **Why:** The views keep their explicit permission and error handling, and the documentation is written to match them.
- **What breaks if changed:** The schema would be empty or wrong for most operations. The Swagger page loads its files from a CDN, so it needs an internet connection.

## Testing

- **Decision:** The test suite uses the same password hasher as the running app, Django's default.
- **Alternative rejected:** A fast hasher such as MD5, switched on only while the tests run.
- **Why:** The tests then run the real hashing code, and nothing in the settings differs between tests and the running app. The cost is a slower suite: a full run takes several minutes, and the slowest tests are the lockout and query count tests that create many users or log in repeatedly.
- **What breaks if changed:** The tests would no longer cover the real hashing path, and a setting that differs by mode is one more thing to keep in sync.

- **Decision:** The tests run on a separate database `test_eventreg` on the same PostgreSQL server and engine as the real one. Only one run happens at a time, and Django clones the test database for the parallel workers inside a run.
- **Alternative rejected:** SQLite for the tests.
- **Why:** The seat lock, the conditional unique constraint and the idempotency insert depend on PostgreSQL behavior, so a different engine would not prove them. Two runs at once would share the same test database.
- **What breaks if changed:** A test could pass on SQLite and fail on the real database.

- **Decision:** Besides the test suite, two scripts run against the live server: a race test for the last seat and a smoke test over every endpoint.
- **Alternative rejected:** Rely on the test suite alone.
- **Why:** The Django test client skips the CSRF check, so 340 tests passed while real HTTP got an HTML 403 on unknown URLs. Only a real request found it.
- **What breaks if changed:** Problems in middleware and request handling would not show until a client hit them.

## Known gaps

- No payments. Events are free.
- No guests per registration.
- No logout or token blacklist.
- Only failed sign-ins are rate limited. Other endpoints are not.
- The sign-in lockout counts by the connecting address, so behind a proxy every client would share one address.
- The Django admin sign-in has no lockout.
- The hidden admin path keeps out scans of `/admin/` and is not access control.
- Expired idempotency records are deleted only when their key is seen again, so a cleanup job would be needed at scale.
- The body size limit reads the `Content-Length` header, so a request that sends no length is not caught.
- No CORS headers, so a browser frontend on another address would be blocked.
- Notifications are in the app only. Nothing is sent by email.
- Anyone can register as an organizer.
- A promoted waitlist attendee has no confirmation window.
- Changes made in the admin bypass the seat rules.
- The Swagger page needs an internet connection.
- Runs on the Django development server and is not set up for production.

## If this grew 10x

- Send notifications and emails from a background worker, with retries.
