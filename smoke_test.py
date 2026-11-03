"""Smoke test over real HTTP. Needs the server running. Standard library only.

Usage: python smoke_test.py [base_url] [--throttle-only]
Creates throwaway data whose names start with smoke_. Run it with DEBUG=True.

A normal run needs a server with throttling off (THROTTLING_ENABLED=False), because the
run sends hundreds of requests from one address. It also checks the request id header
and the /metrics route, which may be on or off.

With --throttle-only the script runs only the rate limit checks. Start the server with
throttling on for that, and wait a minute between runs.

The last section checks retry keys, the sign-in lockout, the body size limit and the
health URL. A run records 9 failed sign-ins from this address, and the address limit is
20 in 15 minutes, so run it at most twice in 15 minutes or clear the LoginFailure rows.
"""
import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
THROTTLE_ONLY = "--throttle-only" in sys.argv
BASE = ARGS[0].rstrip("/") if ARGS else "http://127.0.0.1:8000"
STAMP = str(int(time.time()))
PASSWORD = "Zq7!rvTn#4Lw9dKp"
PHONE = "+2348012345678"
ADMIN_PATH = "eventreg-manage/"  # must match ADMIN_URL in .env

EVENT_KEYS = {
    "id", "title", "description", "category", "event_type", "venue_name", "address",
    "city", "online_link", "start_at", "end_at", "registration_deadline", "capacity",
    "registered_count", "seats_left", "waitlist_count", "contact_email", "contact_phone",
    "organizer_username", "status", "cancel_reason", "created_at",
}
REG_KEYS = {
    "id", "event", "event_title", "event_start_at", "full_name", "phone", "note",
    "status", "waitlist_position", "created_at", "updated_at",
}
ORG_REG_KEYS = REG_KEYS | {"attendee_username", "attendee_email"}
NOTE_KEYS = {"id", "kind", "message", "event", "event_title", "registration", "created_at"}

passed = 0
failed = 0
calls = 0
server_errors = []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stops redirects so a 301 or 302 can be inspected."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class Reply:
    """One answer from the server."""

    def __init__(self, status, body, headers):
        self.s = status
        self.b = body
        self.h = headers

    def short(self):
        text = json.dumps(self.b) if not isinstance(self.b, str) else self.b
        return f"got {self.s} {text[:220]}"


def call(method, path, body=None, token=None, raw=None, params=None, extra=None):
    """Send one request and return a Reply. Counts every 5xx answer."""
    global calls
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    headers = {}
    if raw is not None:
        data = raw.encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    if extra:
        headers.update(extra)
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        resp = OPENER.open(request, timeout=30)
        status, text, hdrs = resp.status, resp.read().decode("utf-8"), resp.headers
    except urllib.error.HTTPError as exc:
        status, text, hdrs = exc.code, exc.read().decode("utf-8"), exc.headers
    except urllib.error.URLError as exc:
        print(f"Cannot reach {url}: {exc.reason}. Is runserver running?")
        sys.exit(2)
    calls += 1
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = text
    if status >= 500:
        server_errors.append(f"{method} {path} -> {status}")
    return Reply(status, parsed, hdrs)


def check(name, ok, reply=None):
    """Print PASS or FAIL and count it."""
    global passed, failed
    if ok:
        passed += 1
        print("PASS " + name)
    else:
        failed += 1
        print("FAIL " + name + ("  (" + reply.short() + ")" if reply else ""))


def err(reply):
    """Return the error object of a reply, or an empty dict."""
    if isinstance(reply.b, dict) and isinstance(reply.b.get("error"), dict):
        return reply.b["error"]
    return {}


def expect(name, reply, status, code=None, field=None):
    """Check status, error code and, for validation errors, a named field."""
    ok = reply.s == status
    if code is not None:
        ok = ok and err(reply).get("code") == code
    if field is not None:
        ok = ok and field in err(reply).get("fields", {})
    check(name, ok, reply)


def is_json(reply):
    """True when the answer says it is JSON."""
    return "application/json" in (reply.h.get("Content-Type") or "")


def need(ok, message):
    """Stop the run when setup failed, because later checks would be noise."""
    if not ok:
        print("SETUP FAILED: " + message)
        sys.exit(2)


def when(dt):
    """ISO text of a datetime with its offset."""
    return dt.isoformat()


def parse(text):
    """Parse an ISO time that may end in Z."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def now_utc():
    return datetime.now(timezone.utc)


def event_body(**over):
    """A valid in person event body, with overrides."""
    start = now_utc() + timedelta(days=10)
    body = {
        "title": f"smoke {STAMP} event",
        "description": "Throwaway event made by the smoke test.",
        "category": "meetup",
        "event_type": "in_person",
        "venue_name": "Smoke Hall",
        "address": "1 Test Street",
        "city": f"SmokeCity{STAMP}",
        "start_at": when(start),
        "end_at": when(start + timedelta(hours=2)),
        "capacity": 5,
        "contact_email": f"smoke_{STAMP}@example.com",
        "contact_phone": PHONE,
    }
    body.update(over)
    return body


def make_user(label, role):
    """Register and log in a throwaway user."""
    username = f"smoke_{label}_{STAMP}"
    r = call("POST", "/api/auth/register/", {
        "username": username, "email": f"{username}@example.com",
        "password": PASSWORD, "role": role})
    check(f"register {label} as {role} answers 201", r.s == 201, r)
    need(r.s == 201, f"cannot register {label}")
    login = call("POST", "/api/auth/login/", {"username": username, "password": PASSWORD})
    need(login.s == 200, f"cannot log in {label}")
    return {"name": username, "id": r.b["id"], "token": login.b["access"], "refresh": login.b["refresh"]}


def make_event(user, **over):
    """Create an event as an organizer and stop if it fails."""
    r = call("POST", "/api/events/", event_body(**over), token=user["token"])
    need(r.s == 201, "cannot create event: " + r.short())
    return r.b


def register(user, event_id, name="Smoke Attendee", extra=None, **over):
    """Register an attendee for an event."""
    body = {"event": event_id, "full_name": name, "phone": PHONE}
    body.update(over)
    return call("POST", "/api/registrations/", body, token=user["token"], extra=extra)


def notes(user):
    """First page of a user's notifications."""
    r = call("GET", "/api/notifications/", token=user["token"])
    return r.b["results"] if r.s == 200 else []


def kinds(user, kind):
    """How many notifications of one kind a user has."""
    return sum(1 for n in notes(user) if n["kind"] == kind)


def sleep_until(moment):
    """Wait until a wall clock time given as epoch seconds."""
    time.sleep(max(0, moment - time.time()))


def is_hex32(value):
    """True for 32 lowercase hexadecimal characters."""
    return isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def finish():
    """Print the totals and exit with the result."""
    print()
    check(f"no answer was a server error across {calls} calls", not server_errors)
    for line in server_errors:
        print("   5xx: " + line)
    print(f"\n{passed} passed, {failed} failed")
    print("Now look at the server terminal. Any line with ERROR or Traceback is a finding.")
    sys.exit(1 if failed else 0)


def throttle_section():
    """Check the rate limits. The server must have throttling on."""
    print("... checking rate limits (the server must have throttling on)")
    user = make_user("thr", "attendee")
    path = "/api/events/"
    limited = None
    last = None
    for _ in range(70):
        last = call("GET", path)
        if last.s == 429:
            limited = last
            break
    check("an anonymous client is limited within 70 reads of the event list", limited is not None, last)
    if limited is None:
        print("   No 429 came back. Start the server with throttling on and try again.")
        return
    check("the 429 answer is JSON with code too_many_requests, the message and no fields",
          is_json(limited) and err(limited) == {"code": "too_many_requests", "message": "Too many requests. Try again later."}, limited)
    retry = limited.h.get("Retry-After")
    check("Retry-After is whole seconds from 1 to 60", retry is not None and retry.isdigit() and 1 <= int(retry) <= 60, limited)
    check("the 429 answer carries a request id", is_hex32(limited.h.get("X-Request-ID")), limited)
    r = call("GET", path, extra={"X-Forwarded-For": "9.9.9.9"})
    check("a forged X-Forwarded-For does not get around the limit", r.s == 429, r)
    r = call("GET", "/api/health/")
    check("health is not limited", r.s == 200, r)
    r = call("GET", "/api/auth/me/")
    check("me is not limited and answers 401 without a token", r.s == 401, r)
    r = call("GET", path, token=user["token"])
    check("a signed-in user has a separate count and is still let through", r.s == 200, r)


print(f"Smoke test against {BASE}, stamp {STAMP}")
if THROTTLE_ONLY:
    throttle_section()
    finish()

# ---------------------------------------------------------------- setup
org1 = make_user("org1", "organizer")
org2 = make_user("org2", "organizer")
org3 = make_user("org3", "organizer")
att1 = make_user("att1", "attendee")
att2 = make_user("att2", "attendee")
att3 = make_user("att3", "attendee")
att4 = make_user("att4", "attendee")

# ---------------------------------------------------------------- auth
r = call("POST", "/api/auth/register/", {
    "username": f"smoke_x_{STAMP}", "email": f"SMOKE_ORG1_{STAMP}@EXAMPLE.COM",
    "password": PASSWORD, "role": "attendee"})
expect("register with a duplicate email in other letter case", r, 400, "validation_error", "email")
r = call("POST", "/api/auth/register/", {
    "username": f"smoke_y_{STAMP}", "email": f"smoke_y_{STAMP}@example.com",
    "password": PASSWORD, "role": "attendee", "is_staff": True, "is_superuser": True, "id": 1})
check("register ignores is_staff, is_superuser and id", r.s == 201 and set(r.b) == {"id", "username", "email", "role"}, r)
check("register answer has no password", r.s == 201 and "password" not in r.b, r)
r = call("POST", "/api/auth/register/", {
    "username": f"smoke_z_{STAMP}", "email": f"smoke_z_{STAMP}@example.com",
    "password": PASSWORD})
expect("register without a role", r, 400, "validation_error", "role")
r = call("POST", "/api/auth/register/", {
    "username": f"smoke_z_{STAMP}", "email": f"smoke_z_{STAMP}@example.com",
    "password": PASSWORD, "role": "admin"})
expect("register with role admin", r, 400, "validation_error", "role")
r = call("POST", "/api/auth/register/", {
    "username": f"smoke_w_{STAMP}", "email": f"smoke_w_{STAMP}@example.com",
    "password": "123", "role": "attendee"})
expect("register with a weak password", r, 400, "validation_error", "password")
r = call("POST", "/api/auth/register/", raw="{bad")
expect("register with malformed JSON", r, 400, "validation_error")

r1 = call("POST", "/api/auth/login/", {"username": org1["name"], "password": "wrong-Pass-1"})
r2 = call("POST", "/api/auth/login/", {"username": "smoke_nobody_" + STAMP, "password": "wrong-Pass-1"})
expect("login with a wrong password", r1, 401, "not_authenticated")
expect("login with an unknown username", r2, 401, "not_authenticated")
check("both login failures give the same answer", r1.b == r2.b, r2)
r = call("POST", "/api/auth/login/", {})
expect("login with an empty body", r, 400, "validation_error")
r = call("POST", "/api/auth/login/", raw="{bad")
expect("login with malformed JSON", r, 400, "validation_error")

r = call("GET", "/api/auth/me/", token=org1["token"])
check("me answers 200 with id, username, email, role", r.s == 200 and set(r.b) == {"id", "username", "email", "role"} and r.b["role"] == "organizer", r)
expect("me without a token", call("GET", "/api/auth/me/"), 401, "not_authenticated")
expect("me with a bad token", call("GET", "/api/auth/me/", token="not.a.token"), 401, "not_authenticated")
expect("me with the refresh token as access token", call("GET", "/api/auth/me/", token=org1["refresh"]), 401, "not_authenticated")
r = call("POST", "/api/auth/refresh/", {"refresh": org1["refresh"]})
check("refresh gives a new access token", r.s == 200 and "access" in r.b, r)
expect("refresh with a bad token", call("POST", "/api/auth/refresh/", {"refresh": "garbage"}), 401, "not_authenticated")
expect("refresh with the access token", call("POST", "/api/auth/refresh/", {"refresh": org1["token"]}), 401, "not_authenticated")

# ---------------------------------------------------------------- generic errors
for method in ("GET", "POST", "PUT", "DELETE"):
    r = call(method, f"/api/smoke-nothing-{STAMP}/")
    check(f"unknown URL with {method} gives JSON not_found", r.s == 404 and err(r).get("code") == "not_found" and is_json(r), r)
r = call("GET", "/" + ADMIN_PATH.rstrip("/"))
check("the admin path without a slash redirects to the path with one", r.s in (301, 302) and (r.h.get("Location") or "").endswith("/" + ADMIN_PATH), r)
r = call("GET", "/" + ADMIN_PATH)
check("the admin is reachable at the configured path", r.s in (200, 302), r)
for old_path in ("/admin", "/admin/", "/admin/login/"):
    r = call("GET", old_path)
    check(f"{old_path} gives JSON not_found", r.s == 404 and err(r).get("code") == "not_found" and is_json(r), r)
r = call("POST", "/admin/login/", {"username": "a", "password": "b"})
check("POST to /admin/login/ gives JSON not_found and not a CSRF page", r.s == 404 and err(r).get("code") == "not_found" and is_json(r), r)
check("/api/schema/ is public", call("GET", "/api/schema/").s == 200)
check("/api/docs/ is public", call("GET", "/api/docs/").s == 200)
r = call("GET", "/api/events/", token="not.a.token")
check("public list ignores a bad token or answers 401 in the standard shape", r.s in (200, 401) and (r.s == 200 or err(r).get("code") == "not_authenticated"), r)

# ---------------------------------------------------------------- event create
r = call("POST", "/api/events/", event_body(), token=org1["token"])
check("create event answers 201", r.s == 201, r)
check("create answer has exactly the event fields", r.s == 201 and set(r.b) == EVENT_KEYS, r)
base_event = r.b
check("new event is scheduled with all seats free", base_event["status"] == "scheduled" and base_event["seats_left"] == 5 and base_event["registered_count"] == 0 and base_event["waitlist_count"] == 0, r)
check("deadline defaults to the start", base_event["registration_deadline"] == base_event["start_at"], r)
check("organizer_username is the caller", base_event["organizer_username"] == org1["name"], r)
check("times come back in UTC", parse(base_event["start_at"]).utcoffset() == timedelta(0), r)

local = now_utc() + timedelta(days=12)
plus_one = timezone(timedelta(hours=1))
r = call("POST", "/api/events/", event_body(
    start_at=when(local.astimezone(plus_one)), end_at=when((local + timedelta(hours=1)).astimezone(plus_one))), token=org1["token"])
check("a +01:00 time is accepted and stored as the same instant in UTC",
      r.s == 201 and parse(r.b["start_at"]) == local.replace(microsecond=local.microsecond) and parse(r.b["start_at"]).utcoffset() == timedelta(0), r)

r = call("POST", "/api/events/", event_body(status="cancelled", cancel_reason="x", organizer=1, id=1), token=org1["token"])
check("create ignores status, cancel_reason, organizer and id", r.s == 201 and r.b["status"] == "scheduled" and r.b["cancel_reason"] == "" and r.b["organizer_username"] == org1["name"] and r.b["id"] != 1, r)

naive = (now_utc() + timedelta(days=9)).replace(tzinfo=None).isoformat()
expect("create with a time without an offset", call("POST", "/api/events/", event_body(start_at=naive), token=org1["token"]), 400, "validation_error", "start_at")
expect("create starting in the past", call("POST", "/api/events/", event_body(start_at=when(now_utc() - timedelta(days=1))), token=org1["token"]), 400, "validation_error", "start_at")
start = now_utc() + timedelta(days=9)
expect("create with end before start", call("POST", "/api/events/", event_body(start_at=when(start), end_at=when(start - timedelta(hours=1))), token=org1["token"]), 400, "validation_error", "end_at")
expect("create with a deadline after the start", call("POST", "/api/events/", event_body(start_at=when(start), end_at=when(start + timedelta(hours=1)), registration_deadline=when(start + timedelta(minutes=5))), token=org1["token"]), 400, "validation_error", "registration_deadline")
expect("create online without a link", call("POST", "/api/events/", event_body(event_type="online", online_link=""), token=org1["token"]), 400, "validation_error", "online_link")
expect("create in person without a city", call("POST", "/api/events/", event_body(city=""), token=org1["token"]), 400, "validation_error", "city")
expect("create hybrid without an address", call("POST", "/api/events/", event_body(event_type="hybrid", online_link="https://example.com/x", address=""), token=org1["token"]), 400, "validation_error", "address")
expect("create with an ftp link", call("POST", "/api/events/", event_body(event_type="online", online_link="ftp://example.com/x"), token=org1["token"]), 400, "validation_error", "online_link")
r = call("POST", "/api/events/", event_body(event_type="online", online_link="https://example.com/live", venue_name="", address="", city=""), token=org1["token"])
check("online event needs no venue fields", r.s == 201, r)
expect("create with a phone starting with 0", call("POST", "/api/events/", event_body(contact_phone="08012345678"), token=org1["token"]), 400, "validation_error", "contact_phone")
expect("create with spaces in the phone", call("POST", "/api/events/", event_body(contact_phone="+234 801 234 5678"), token=org1["token"]), 400, "validation_error", "contact_phone")
expect("create with a 2 character title", call("POST", "/api/events/", event_body(title="ab"), token=org1["token"]), 400, "validation_error", "title")
expect("create with a null character in the title", call("POST", "/api/events/", event_body(title="bad\u0000title"), token=org1["token"]), 400, "validation_error", "title")
expect("create with a null character in the description", call("POST", "/api/events/", event_body(description="bad\u0000text"), token=org1["token"]), 400, "validation_error", "description")
expect("create with a 201 character title", call("POST", "/api/events/", event_body(title="t" * 201), token=org1["token"]), 400, "validation_error", "title")
expect("create with a 201 character city", call("POST", "/api/events/", event_body(city="c" * 101), token=org1["token"]), 400, "validation_error", "city")
for bad in (0, -1, 100001, 2147483648, 99999999999999999999, "abc", None, 1.5):
    expect(f"create with capacity {bad!r}", call("POST", "/api/events/", event_body(capacity=bad), token=org1["token"]), 400, "validation_error", "capacity")
r = call("POST", "/api/events/", {}, token=org1["token"])
check("create with an empty body names the required fields", r.s == 400 and {"title", "description", "category", "event_type", "start_at", "end_at", "capacity", "contact_email", "contact_phone"} <= set(err(r).get("fields", {})), r)
expect("create with malformed JSON", call("POST", "/api/events/", raw="{bad", token=org1["token"]), 400, "validation_error")
expect("create as an attendee", call("POST", "/api/events/", event_body(), token=att1["token"]), 403, "permission_denied")
expect("create without a token", call("POST", "/api/events/", event_body()), 401, "not_authenticated")
expect("PUT on the event collection", call("PUT", "/api/events/", event_body(), token=org1["token"]), 405, "method_not_allowed")

# ---------------------------------------------------------------- event edit
eid = base_event["id"]
r = call("PATCH", f"/api/events/{eid}/", {"title": f"smoke {STAMP} renamed"}, token=org1["token"])
check("edit the title", r.s == 200 and r.b["title"] == f"smoke {STAMP} renamed" and set(r.b) == EVENT_KEYS, r)
r = call("PATCH", f"/api/events/{eid}/", {"status": "cancelled", "organizer": 1, "cancel_reason": "x"}, token=org1["token"])
check("edit ignores status, organizer and cancel_reason", r.s == 200 and r.b["status"] == "scheduled" and r.b["organizer_username"] == org1["name"] and r.b["cancel_reason"] == "", r)
expect("edit end_at before the saved start", call("PATCH", f"/api/events/{eid}/", {"end_at": when(now_utc() + timedelta(days=1))}, token=org1["token"]), 400, "validation_error", "end_at")
expect("edit start_at into the past", call("PATCH", f"/api/events/{eid}/", {"start_at": when(now_utc() - timedelta(days=1))}, token=org1["token"]), 400, "validation_error", "start_at")
expect("edit capacity to 0", call("PATCH", f"/api/events/{eid}/", {"capacity": 0}, token=org1["token"]), 400, "validation_error", "capacity")
expect("edit capacity to a huge number", call("PATCH", f"/api/events/{eid}/", {"capacity": 99999999999999999999}, token=org1["token"]), 400, "validation_error", "capacity")
expect("edit event_type to online without a link", call("PATCH", f"/api/events/{eid}/", {"event_type": "online"}, token=org1["token"]), 400, "validation_error", "online_link")
r = call("PATCH", f"/api/events/{eid}/", {"title": "should not be saved", "end_at": when(now_utc())}, token=org1["token"])
after = call("GET", f"/api/events/{eid}/")
check("a failed edit saves nothing", r.s == 400 and after.b["title"] == f"smoke {STAMP} renamed", after)
expect("edit with malformed JSON", call("PATCH", f"/api/events/{eid}/", raw="{bad", token=org1["token"]), 400, "validation_error")
expect("edit another organizer's event", call("PATCH", f"/api/events/{eid}/", {"title": "stolen"}, token=org2["token"]), 404, "not_found")
expect("edit as an attendee", call("PATCH", f"/api/events/{eid}/", {"title": "x" * 5}, token=att1["token"]), 403, "permission_denied")
expect("edit without a token", call("PATCH", f"/api/events/{eid}/", {"title": "x" * 5}), 401, "not_authenticated")
expect("edit an unknown event", call("PATCH", "/api/events/999999999/", {"title": "x" * 5}, token=org1["token"]), 404, "not_found")
expect("PUT on an event", call("PUT", f"/api/events/{eid}/", event_body(), token=org1["token"]), 405, "method_not_allowed")
expect("DELETE on an event", call("DELETE", f"/api/events/{eid}/", token=org1["token"]), 405, "method_not_allowed")

# ---------------------------------------------------------------- huge and odd ids
huge = "99999999999999999999"
for label, method, path, token in (
    ("event detail", "GET", f"/api/events/{huge}/", None),
    ("event edit", "PATCH", f"/api/events/{huge}/", org1["token"]),
    ("event cancel", "POST", f"/api/events/{huge}/cancel/", org1["token"]),
    ("event registrations", "GET", f"/api/events/{huge}/registrations/", org1["token"]),
    ("registration detail", "GET", f"/api/registrations/{huge}/", att1["token"]),
    ("registration cancel", "POST", f"/api/registrations/{huge}/cancel/", att1["token"]),
):
    body = {"title": "valid title", "reason": "x"} if method in ("PATCH", "POST") else None
    expect(f"{label} with a huge id", call(method, path, body, token=token), 404, "not_found")
expect("event detail with a text id", call("GET", "/api/events/abc/"), 404, "not_found")
expect("event detail with an unknown id", call("GET", "/api/events/999999999/"), 404, "not_found")

# ---------------------------------------------------------------- reads and filters
r = call("GET", f"/api/events/{eid}/")
check("detail is public and has the event fields", r.s == 200 and set(r.b) == EVENT_KEYS, r)
r = call("GET", "/api/events/")
check("list is public and paged", r.s == 200 and set(r.b) == {"count", "next", "previous", "results"} and len(r.b["results"]) <= 10, r)
check("list items have the event fields", r.s == 200 and all(set(i) == EVENT_KEYS for i in r.b["results"]), r)
starts = [i["start_at"] for i in r.b["results"]]
check("list is ordered by start ascending", starts == sorted(starts), r)
r = call("GET", "/api/events/", params={"q": STAMP})
check("q finds the smoke events", r.s == 200 and r.b["count"] >= 2 and all(STAMP in (i["title"] + i["description"] + i["city"]) or "smoke" in i["description"].lower() for i in r.b["results"]), r)
r = call("GET", "/api/events/", params={"q": f"SMOKE {STAMP} RENAMED"})
check("q ignores letter case", r.s == 200 and r.b["count"] == 1, r)
r = call("GET", "/api/events/", params={"city": f"smokecity{STAMP}".upper()})
check("city filter ignores letter case", r.s == 200 and r.b["count"] >= 1 and all(f"SmokeCity{STAMP}".lower() in i["city"].lower() for i in r.b["results"]), r)
r = call("GET", "/api/events/", params={"city": f"SmokeCity{STAMP}", "event_type": "in_person", "category": "meetup"})
check("filters combine", r.s == 200 and r.b["count"] >= 1 and all(i["event_type"] == "in_person" and i["category"] == "meetup" for i in r.b["results"]), r)
r = call("GET", "/api/events/", params={"q": STAMP, "event_type": "online"})
check("event_type=online finds the online event only", r.s == 200 and r.b["count"] == 1 and r.b["results"][0]["event_type"] == "online", r)
for param, value in (("category", "nope"), ("event_type", "nope"), ("status", "nope"),
                     ("starts_after", "2026-13-45"), ("starts_before", "yesterday"),
                     ("q", "a\u0000b"), ("city", "a\u0000b")):
    expect(f"list with {param}={value!r}", call("GET", "/api/events/", params={param: value}), 400, "validation_error", param)
check("starts_after=0001-01-01 works", call("GET", "/api/events/", params={"starts_after": "0001-01-01"}).s == 200)
check("starts_before=9999-12-31 works", call("GET", "/api/events/", params={"starts_before": "9999-12-31"}).s == 200)
check("starts_before=0001-01-01 works", call("GET", "/api/events/", params={"starts_before": "0001-01-01"}).s == 200)
check("starts_after=9999-12-31 works", call("GET", "/api/events/", params={"starts_after": "9999-12-31"}).s == 200)
day = (now_utc() + timedelta(days=10)).date().isoformat()
r = call("GET", "/api/events/", params={"q": STAMP, "starts_after": day, "starts_before": day})
check("the date filters include the given day", r.s == 200 and r.b["count"] >= 1, r)
r = call("GET", "/api/events/", params={"q": "' OR 1=1 --"})
check("quotes in q are plain text", r.s == 200 and r.b["count"] == 0, r)
check("a very long q is fine", call("GET", "/api/events/", params={"q": "a" * 5000}).s == 200)
check("unicode in q is fine", call("GET", "/api/events/", params={"q": "caf\u00e9 \u4e2d\u6587"}).s == 200)
check("unknown and empty parameters are ignored", call("GET", "/api/events/", params={"zzz": "1", "q": "", "city": ""}).s == 200)
r = call("GET", "/api/events/", params={"page_size": 100})
check("the client cannot change the page size", r.s == 200 and len(r.b["results"]) <= 10, r)
for page in ("0", "-1", "abc", "99999", huge):
    expect(f"list with page={page}", call("GET", "/api/events/", params={"page": page}), 404, "not_found")

# paging with more than ten events
for n in range(11):
    make_event(org3, title=f"smoke {STAMP} page {n:02d}")
r1 = call("GET", "/api/my/events/", token=org3["token"])
r2 = call("GET", "/api/my/events/", params={"page": 2}, token=org3["token"])
check("my events: 11 events, page 1 has 10 and a next link", r1.s == 200 and r1.b["count"] == 11 and len(r1.b["results"]) == 10 and r1.b["next"], r1)
check("my events: page 2 has 1 and a previous link", r2.s == 200 and len(r2.b["results"]) == 1 and r2.b["previous"], r2)
expect("my events: page 3 does not exist", call("GET", "/api/my/events/", params={"page": 3}, token=org3["token"]), 404, "not_found")
starts = [i["start_at"] for i in r1.b["results"]]
check("my events are ordered newest start first", starts == sorted(starts, reverse=True), r1)
r = call("GET", "/api/my/events/", token=org2["token"])
check("my events shows only the caller's events", r.s == 200 and all(i["organizer_username"] == org2["name"] for i in r.b["results"]), r)
expect("my events as an attendee", call("GET", "/api/my/events/", token=att1["token"]), 403, "permission_denied")
expect("my events without a token", call("GET", "/api/my/events/"), 401, "not_authenticated")

# ---------------------------------------------------------------- registrations: waitlist and promotion
e1 = make_event(org1, title=f"smoke {STAMP} cap one", capacity=1)
e1id = e1["id"]
r = register(att1, e1id, "Att One")
check("first attendee gets the seat", r.s == 201 and r.b["status"] == "registered" and r.b["waitlist_position"] is None and set(r.b) == REG_KEYS, r)
reg1 = r.b["id"]
check("registration read shape names the event", r.s == 201 and r.b["event"] == e1id and r.b["event_title"] == e1["title"], r)
expect("register again for the same event", register(att1, e1id), 400, "already_registered")
r = register(att2, e1id, "Att Two")
check("second attendee is waitlisted at position 1", r.s == 201 and r.b["status"] == "waitlisted" and r.b["waitlist_position"] == 1, r)
reg2 = r.b["id"]
r = register(att3, e1id, "Att Three")
check("third attendee is waitlisted at position 2", r.s == 201 and r.b["status"] == "waitlisted" and r.b["waitlist_position"] == 2, r)
reg3 = r.b["id"]
r = register(att4, e1id, "Att Four", status="registered", attendee=1)
check("register ignores status and attendee in the body", r.s == 201 and r.b["status"] == "waitlisted" and r.b["waitlist_position"] == 3, r)
reg4 = r.b["id"]
r = call("GET", f"/api/events/{e1id}/")
check("event counts are real", r.b["registered_count"] == 1 and r.b["waitlist_count"] == 3 and r.b["seats_left"] == 0, r)

expect("register as an organizer", register(org2, e1id), 403, "permission_denied")
expect("register without a token", call("POST", "/api/registrations/", {"event": e1id, "full_name": "Ab", "phone": PHONE}), 401, "not_authenticated")
for bad in (None, "abc", 99999999999, 999999999, -1, 1.5):
    expect(f"register with event {bad!r}", call("POST", "/api/registrations/", {"event": bad, "full_name": "Ab", "phone": PHONE}, token=att1["token"]), 400, "validation_error", "event")
expect("register without an event", call("POST", "/api/registrations/", {"full_name": "Ab", "phone": PHONE}, token=att1["token"]), 400, "validation_error", "event")
expect("register with a 1 character name", register(att1, e1id, "A"), 400, "validation_error", "full_name")
expect("register with a 121 character name", register(att1, e1id, "n" * 121), 400, "validation_error", "full_name")
expect("register with a null character in the name", register(att1, e1id, "Bad\u0000Name"), 400, "validation_error", "full_name")
expect("register with a bad phone", register(att1, e1id, phone="12345"), 400, "validation_error", "phone")
expect("register with a 501 character note", register(att1, e1id, note="n" * 501), 400, "validation_error", "note")
expect("register with a null character in the note", register(att1, e1id, note="a\u0000b"), 400, "validation_error", "note")
expect("register with malformed JSON", call("POST", "/api/registrations/", raw="{bad", token=att1["token"]), 400, "validation_error")
expect("PUT on the registration collection", call("PUT", "/api/registrations/", {}, token=att1["token"]), 405, "method_not_allowed")
expect("DELETE on a registration", call("DELETE", f"/api/registrations/{reg1}/", token=att1["token"]), 405, "method_not_allowed")

r = call("GET", "/api/registrations/", token=att1["token"])
check("attendee list is paged with the registration fields", r.s == 200 and set(r.b) == {"count", "next", "previous", "results"} and all(set(i) == REG_KEYS for i in r.b["results"]), r)
r = call("GET", "/api/registrations/", params={"status": "waitlisted"}, token=att2["token"])
check("attendee list filters by status", r.s == 200 and r.b["count"] == 1 and r.b["results"][0]["status"] == "waitlisted", r)
expect("attendee list with a bad status", call("GET", "/api/registrations/", params={"status": "nope"}, token=att1["token"]), 400, "validation_error", "status")
check("attendee list ignores an empty status", call("GET", "/api/registrations/", params={"status": ""}, token=att1["token"]).s == 200)
expect("attendee list page 99999", call("GET", "/api/registrations/", params={"page": 99999}, token=att1["token"]), 404, "not_found")
expect("attendee list as an organizer", call("GET", "/api/registrations/", token=org1["token"]), 403, "permission_denied")
expect("attendee list without a token", call("GET", "/api/registrations/"), 401, "not_authenticated")
r = call("GET", f"/api/registrations/{reg1}/", token=att1["token"])
check("registration detail for the owner", r.s == 200 and r.b["id"] == reg1, r)
expect("registration detail for another attendee", call("GET", f"/api/registrations/{reg1}/", token=att2["token"]), 404, "not_found")
expect("registration detail as an organizer", call("GET", f"/api/registrations/{reg1}/", token=org1["token"]), 403, "permission_denied")

r = call("GET", f"/api/events/{e1id}/registrations/", token=org1["token"])
check("organizer list shows attendee username and email", r.s == 200 and r.b["count"] == 4 and all(set(i) == ORG_REG_KEYS for i in r.b["results"]), r)
created = [i["created_at"] for i in r.b["results"]]
check("organizer list is oldest first", created == sorted(created), r)
expect("organizer list for another organizer's event", call("GET", f"/api/events/{e1id}/registrations/", token=org2["token"]), 404, "not_found")
expect("organizer list as an attendee", call("GET", f"/api/events/{e1id}/registrations/", token=att1["token"]), 403, "permission_denied")
expect("organizer list without a token", call("GET", f"/api/events/{e1id}/registrations/"), 401, "not_authenticated")
expect("organizer list for an unknown event", call("GET", "/api/events/999999999/registrations/", token=org1["token"]), 404, "not_found")

n1, n2 = notes(att1), notes(att2)
check("seat holder got one registration_confirmed notification", [n["kind"] for n in n1] == ["registration_confirmed"] and set(n1[0]) == NOTE_KEYS and n1[0]["message"] == f"You are registered for {e1['title']}.", None)
check("waitlisted attendee got one waitlisted notification", [n["kind"] for n in n2] == ["waitlisted"] and n2[0]["message"] == f"You are on the waitlist for {e1['title']}.", None)
r = call("GET", "/api/notifications/", token=org1["token"])
check("the organizer gets no notifications", r.s == 200 and r.b["count"] == 0, r)
expect("notifications without a token", call("GET", "/api/notifications/"), 401, "not_authenticated")
expect("notifications page 99999", call("GET", "/api/notifications/", params={"page": 99999}, token=att1["token"]), 404, "not_found")
expect("POST on notifications", call("POST", "/api/notifications/", {}, token=att1["token"]), 405, "method_not_allowed")

# cancel and promotion
expect("another attendee cancels my registration", call("POST", f"/api/registrations/{reg1}/cancel/", token=att2["token"]), 404, "not_found")
expect("organizer cancels a registration", call("POST", f"/api/registrations/{reg1}/cancel/", token=org1["token"]), 403, "permission_denied")
r = call("POST", f"/api/registrations/{reg1}/cancel/", token=att1["token"])
check("owner cancels the seat", r.s == 200 and r.b["status"] == "cancelled" and set(r.b) == REG_KEYS, r)
r = call("GET", f"/api/registrations/{reg2}/", token=att2["token"])
check("the oldest waitlisted attendee is promoted", r.s == 200 and r.b["status"] == "registered" and r.b["waitlist_position"] is None, r)
check("the promoted attendee got a promoted notification", kinds(att2, "promoted") == 1 and any(n["message"] == f"A seat opened up and you are now registered for {e1['title']}." for n in notes(att2)), None)
r = call("GET", f"/api/registrations/{reg3}/", token=att3["token"])
check("the waitlist moves up", r.s == 200 and r.b["waitlist_position"] == 1, r)
check("the attendee who cancelled got no new notification", len(notes(att1)) == 1, None)
n2 = notes(att2)
stamps = [n["created_at"] for n in n2]
check("notifications are newest first", stamps == sorted(stamps, reverse=True), None)
expect("cancel the same registration twice", call("POST", f"/api/registrations/{reg1}/cancel/", token=att1["token"]), 400, "already_cancelled")
r = register(att1, e1id, "Att One Again")
check("a cancelled attendee can register again as a new row", r.s == 201 and r.b["status"] == "waitlisted" and r.b["id"] != reg1 and r.b["waitlist_position"] == 3, r)
reg1b = r.b["id"]
r = call("GET", "/api/registrations/", token=att1["token"])
check("attendee list is newest first and keeps the old row", r.s == 200 and r.b["count"] == 2 and r.b["results"][0]["id"] == reg1b, r)
r = call("POST", f"/api/registrations/{reg4}/cancel/", token=att4["token"])
check("cancelling a waitlisted registration works", r.s == 200 and r.b["status"] == "cancelled", r)
r = call("GET", f"/api/registrations/{reg3}/", token=att3["token"])
check("cancelling a waitlisted registration promotes nobody", r.s == 200 and r.b["status"] == "waitlisted", r)

# capacity changes and update notifications
r = call("PATCH", f"/api/events/{e1id}/", {"capacity": 2}, token=org1["token"])
check("raising capacity promotes the next attendee", r.s == 200 and r.b["registered_count"] == 2 and r.b["waitlist_count"] == 1, r)
r = call("GET", f"/api/registrations/{reg3}/", token=att3["token"])
check("the promoted attendee is registered", r.s == 200 and r.b["status"] == "registered", r)
check("the capacity promotion wrote a promoted notification", kinds(att3, "promoted") == 1, None)
r = call("PATCH", f"/api/events/{e1id}/", {"capacity": 1}, token=org1["token"])
expect("lower capacity below the registered count", r, 400, "capacity_too_low")
check("capacity_too_low saved nothing", call("GET", f"/api/events/{e1id}/").b["capacity"] == 2)
expect("capacity 0 wins over capacity_too_low", call("PATCH", f"/api/events/{e1id}/", {"capacity": 0}, token=org1["token"]), 400, "validation_error", "capacity")
check("capacity equal to the registered count is allowed", call("PATCH", f"/api/events/{e1id}/", {"capacity": 2}, token=org1["token"]).s == 200)
before = kinds(att2, "event_updated")
r = call("PATCH", f"/api/events/{e1id}/", {"city": f"Moved{STAMP}"}, token=org1["token"])
check("changing the city answers 200", r.s == 200 and r.b["city"] == f"Moved{STAMP}", r)
check("registered attendees get one event_updated", kinds(att2, "event_updated") == before + 1 and kinds(att3, "event_updated") == 1, None)
check("waitlisted attendees get one event_updated", kinds(att1, "event_updated") == 1, None)
check("a cancelled attendee gets no event_updated", kinds(att4, "event_updated") == 0, None)
call("PATCH", f"/api/events/{e1id}/", {"title": f"smoke {STAMP} cap one renamed", "description": "changed"}, token=org1["token"])
call("PATCH", f"/api/events/{e1id}/", {"city": f"Moved{STAMP}"}, token=org1["token"])
check("a title change and an unchanged value send nothing", kinds(att2, "event_updated") == before + 1, None)
shifted = parse(call("GET", f"/api/events/{e1id}/").b["start_at"]).astimezone(plus_one)
call("PATCH", f"/api/events/{e1id}/", {"start_at": when(shifted)}, token=org1["token"])
check("the same instant with another offset sends nothing", kinds(att2, "event_updated") == before + 1, None)

# ---------------------------------------------------------------- event cancel
e2 = make_event(org1, title=f"smoke {STAMP} to cancel", capacity=1)
e2id = e2["id"]
ra = register(att1, e2id, "Att One").b
rb = register(att2, e2id, "Att Two").b
expect("cancel event as an attendee", call("POST", f"/api/events/{e2id}/cancel/", {"reason": "x"}, token=att1["token"]), 403, "permission_denied")
expect("cancel event without a token", call("POST", f"/api/events/{e2id}/cancel/", {"reason": "x"}), 401, "not_authenticated")
expect("cancel another organizer's event", call("POST", f"/api/events/{e2id}/cancel/", {"reason": "x"}, token=org2["token"]), 404, "not_found")
expect("cancel event without a reason", call("POST", f"/api/events/{e2id}/cancel/", {}, token=org1["token"]), 400, "validation_error", "reason")
expect("cancel event with a blank reason", call("POST", f"/api/events/{e2id}/cancel/", {"reason": "   "}, token=org1["token"]), 400, "validation_error", "reason")
expect("cancel event with a 501 character reason", call("POST", f"/api/events/{e2id}/cancel/", {"reason": "r" * 501}, token=org1["token"]), 400, "validation_error", "reason")
expect("cancel event with a null character", call("POST", f"/api/events/{e2id}/cancel/", {"reason": "a\u0000b"}, token=org1["token"]), 400, "validation_error", "reason")
expect("GET on the cancel route", call("GET", f"/api/events/{e2id}/cancel/", token=org1["token"]), 405, "method_not_allowed")
check("failed cancels left the event scheduled", call("GET", f"/api/events/{e2id}/").b["status"] == "scheduled")
r = call("POST", f"/api/events/{e2id}/cancel/", {"reason": "  Venue unavailable  ", "status": "scheduled"}, token=org1["token"])
check("cancel event answers 200 with the trimmed reason", r.s == 200 and r.b["status"] == "cancelled" and r.b["cancel_reason"] == "Venue unavailable" and set(r.b) == EVENT_KEYS, r)
check("cancelled event shows no registered or waitlisted attendees", r.b["registered_count"] == 0 and r.b["waitlist_count"] == 0 and r.b["seats_left"] == r.b["capacity"], r)
check("both open registrations became event_cancelled", call("GET", f"/api/registrations/{ra['id']}/", token=att1["token"]).b["status"] == "event_cancelled" and call("GET", f"/api/registrations/{rb['id']}/", token=att2["token"]).b["status"] == "event_cancelled")
check("both attendees were told, with the reason", any(n["message"] == f"{e2['title']} has been cancelled. Reason: Venue unavailable" for n in notes(att1)) and kinds(att2, "event_cancelled") == 1, None)
expect("cancel the event again", call("POST", f"/api/events/{e2id}/cancel/", {"reason": "again"}, token=org1["token"]), 400, "event_cancelled")
expect("edit a cancelled event", call("PATCH", f"/api/events/{e2id}/", {"title": "new title"}, token=org1["token"]), 400, "event_cancelled")
expect("register for a cancelled event", register(att3, e2id), 400, "event_cancelled")
expect("cancel a registration of a cancelled event", call("POST", f"/api/registrations/{ra['id']}/cancel/", token=att1["token"]), 400, "already_cancelled")
check("the cancelled event is still readable", call("GET", f"/api/events/{e2id}/").b["status"] == "cancelled")
r = call("GET", "/api/events/", params={"q": f"smoke {STAMP} to cancel"})
check("the default list hides the cancelled event", r.s == 200 and r.b["count"] == 0, r)
r = call("GET", "/api/events/", params={"q": f"smoke {STAMP} to cancel", "status": "cancelled"})
check("status=cancelled lists it", r.s == 200 and r.b["count"] == 1, r)
r = call("GET", "/api/events/", params={"q": f"smoke {STAMP} to cancel", "status": "scheduled"})
check("status=scheduled behaves like the default", r.s == 200 and r.b["count"] == 0, r)
check("the organizer still has no notifications", call("GET", "/api/notifications/", token=org1["token"]).b["count"] == 0)

long_title = ("smoke " + STAMP + " " + "T" * 200)[:200]
e4 = make_event(org1, title=long_title, capacity=2)
check("a 200 character title can be registered for", register(att3, e4["id"], "Att Three").s == 201)
r = call("POST", f"/api/events/{e4['id']}/cancel/", {"reason": "R" * 500}, token=org1["token"])
check("cancel with a 500 character reason works", r.s == 200, r)
long_notes = [n for n in notes(att3) if n["kind"] == "event_cancelled"]
check("a long cancel message is cut to 255 characters", len(long_notes) == 1 and len(long_notes[0]["message"]) == 255, None)

# ---------------------------------------------------------------- deadline and started event
t0 = time.time()
start = datetime.fromtimestamp(t0 + 14, tz=timezone.utc)
e3 = make_event(org1, title=f"smoke {STAMP} short", capacity=5, start_at=when(start),
                end_at=when(start + timedelta(hours=1)),
                registration_deadline=when(datetime.fromtimestamp(t0 + 7, tz=timezone.utc)))
e3id = e3["id"]
rs1 = register(att1, e3id, "Att One")
rs3 = register(att3, e3id, "Att Three")
check("registering before the deadline works", rs1.s == 201 and rs3.s == 201, rs1)
print("... waiting for the deadline to pass")
sleep_until(t0 + 8.5)
expect("register after the deadline", register(att2, e3id), 400, "registration_closed")
r = call("POST", f"/api/registrations/{rs1.b['id']}/cancel/", token=att1["token"])
check("cancelling after the deadline is allowed", r.s == 200 and r.b["status"] == "cancelled", r)
check("editing after the deadline is allowed", call("PATCH", f"/api/events/{e3id}/", {"title": f"smoke {STAMP} short edited"}, token=org1["token"]).s == 200)
print("... waiting for the event to start")
sleep_until(t0 + 15.5)
expect("register for a started event", register(att2, e3id), 400, "event_started")
expect("cancel a registration of a started event", call("POST", f"/api/registrations/{rs3.b['id']}/cancel/", token=att3["token"]), 400, "event_started")
expect("edit a started event", call("PATCH", f"/api/events/{e3id}/", {"title": "late change"}, token=org1["token"]), 400, "event_started")
expect("cancel a started event", call("POST", f"/api/events/{e3id}/cancel/", {"reason": "late"}, token=org1["token"]), 400, "event_started")
check("a started event is still readable", call("GET", f"/api/events/{e3id}/").s == 200)
r = call("GET", "/api/events/", params={"q": f"smoke {STAMP} short"})
check("a started event leaves the default list", r.s == 200 and r.b["count"] == 0, r)

# ---------------------------------------------------------------- retry keys, lockout, size, health
def idem(user, key, body, path="/api/events/"):
    """POST with an Idempotency-Key header as the given user."""
    return call("POST", path, body, token=user["token"] if user else None, extra={"Idempotency-Key": key})


def big_body(method, path, length):
    """Send a real body of the given size and return a Reply. The server may answer before it has read it all."""
    global calls
    parts = urllib.parse.urlsplit(BASE)
    klass = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = klass(parts.hostname, parts.port, timeout=60)
    try:
        conn.request(method, path, body=b"x" * length, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        text = resp.read().decode("utf-8")
        status, hdrs = resp.status, resp.headers
    except OSError as exc:
        print(f"Cannot reach {BASE}: {exc}. Is runserver running?")
        sys.exit(2)
    finally:
        conn.close()
    calls += 1
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = text
    if status >= 500:
        server_errors.append(f"{method} {path} -> {status}")
    return Reply(status, parsed, hdrs)


print("... checking retry keys, lockout, body size and health")

# health
r = call("GET", "/api/health/")
check("health answers 200 with status ok", r.s == 200 and r.b == {"status": "ok"} and is_json(r), r)
check("health ignores a bad token", call("GET", "/api/health/", token="not.a.token").s == 200)
expect("POST on health", call("POST", "/api/health/", {}), 405, "method_not_allowed")

# schema
schema = call("GET", "/api/schema/", params={"format": "json"})
paths = schema.b.get("paths", {}) if isinstance(schema.b, dict) else {}
operations = sum(1 for item in paths.values() for verb in item if verb in ("get", "post", "put", "patch", "delete"))
check("the schema has 16 operations", operations == 16, schema)
check("health is not in the schema", "/api/health/" not in paths)
check("metrics is not in the schema", "/metrics" not in paths)
for route in ("/api/events/", "/api/registrations/"):
    post = paths.get(route, {}).get("post", {})
    has_header = any(p.get("name") == "Idempotency-Key" and p.get("in") == "header" for p in post.get("parameters", []))
    check(f"POST {route} documents the Idempotency-Key header and a 422", has_header and "422" in post.get("responses", {}))

# retry keys on event create
iorg = make_user("idemorg", "organizer")
iatt = make_user("idematt", "attendee")
iatt2 = make_user("idematt2", "attendee")
ititle = f"smoke {STAMP} idem event"
ibody = event_body(title=ititle, capacity=3)
key1 = f"smoke-{STAMP}-ev1"
r1 = idem(iorg, key1, ibody)
check("first use of a key creates the event", r1.s == 201 and not r1.h.get("Idempotent-Replayed"), r1)
r2 = idem(iorg, key1, ibody)
check("the same key and body replays the first answer", r2.s == 201 and r2.b == r1.b and r2.h.get("Idempotent-Replayed") == "true", r2)
r = call("GET", "/api/events/", params={"q": ititle})
check("the replay created no second event", r.s == 200 and r.b["count"] == 1, r)
expect("the same key with another body", idem(iorg, key1, dict(ibody, capacity=4)), 422, "idempotency_key_reused")
key2 = f"smoke-{STAMP}-ev2"
expect("a failed create is answered as usual", idem(iorg, key2, dict(ibody, title=ititle + " b", capacity=0)), 400, "validation_error", "capacity")
r = idem(iorg, key2, dict(ibody, title=ititle + " b"))
check("a failed answer is not stored, so the key works again", r.s == 201 and not r.h.get("Idempotent-Replayed"), r)
for label, value in (("a space", "bad key"), ("an empty value", ""), ("65 characters", "k" * 65), ("a slash", "a/b")):
    expect(f"key with {label}", idem(iorg, value, ibody), 400, "validation_error", "Idempotency-Key")
r = idem(iorg, "k" * 64, dict(ibody, title=ititle + " c"))
check("a key of 64 characters is accepted", r.s == 201, r)
expect("a bad key without a token", call("POST", "/api/events/", ibody, extra={"Idempotency-Key": "bad key"}), 401, "not_authenticated")
expect("a bad key as an attendee", idem(iatt, "bad key", ibody), 403, "permission_denied")
check("a bad key on another endpoint is ignored", call("GET", "/api/events/", extra={"Idempotency-Key": "bad key"}).s == 200)

# retry keys on registration create
ev = make_event(iorg, title=f"smoke {STAMP} idem reg", capacity=3)
rbody = {"event": ev["id"], "full_name": "Idem One", "phone": PHONE}
rkey = f"smoke-{STAMP}-rg1"
a1 = idem(iatt, rkey, rbody, "/api/registrations/")
check("first use of a key registers", a1.s == 201 and a1.b["status"] == "registered" and not a1.h.get("Idempotent-Replayed"), a1)
a2 = idem(iatt, rkey, rbody, "/api/registrations/")
check("the same key and body replays the registration", a2.s == 201 and a2.b == a1.b and a2.h.get("Idempotent-Replayed") == "true", a2)
r = call("GET", "/api/registrations/", token=iatt["token"])
check("the replay created no second registration", r.s == 200 and r.b["count"] == 1, r)
check("the replay took no second seat", call("GET", f"/api/events/{ev['id']}/").b["registered_count"] == 1)
expect("a retry without a key is still already_registered", register(iatt, ev["id"], "Idem One"), 400, "already_registered")
expect("the same key with another body", idem(iatt, rkey, dict(rbody, full_name="Someone Else"), "/api/registrations/"), 422, "idempotency_key_reused")
b1 = idem(iatt2, rkey, dict(rbody, full_name="Idem Two"), "/api/registrations/")
check("another user can use the same key", b1.s == 201 and not b1.h.get("Idempotent-Replayed") and b1.b["id"] != a1.b["id"], b1)
r = call("POST", f"/api/registrations/{a1.b['id']}/cancel/", token=iatt["token"])
check("the first registration is cancelled", r.s == 200 and r.b["status"] == "cancelled", r)
a3 = idem(iatt, rkey, rbody, "/api/registrations/")
check("a replay shows the original answer, not the current state", a3.s == 201 and a3.b == a1.b and a3.b["status"] == "registered" and a3.h.get("Idempotent-Replayed") == "true", a3)
rkey2 = f"smoke-{STAMP}-rg2"
expect("a failed registration is answered as usual", idem(iatt, rkey2, dict(rbody, phone="12345"), "/api/registrations/"), 400, "validation_error", "phone")
r = idem(iatt, rkey2, rbody, "/api/registrations/")
check("a failed registration is not stored, so the key works again", r.s == 201 and not r.h.get("Idempotent-Replayed"), r)

# simultaneous requests with one key
ckey = f"smoke-{STAMP}-race"
ctitle = f"smoke {STAMP} idem race"
cbody = event_body(title=ctitle, capacity=2)
race_results = []
threads = [threading.Thread(target=lambda: race_results.append(idem(iorg, ckey, cbody))) for _ in range(6)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("six simultaneous requests with one key all answer 201", len(race_results) == 6 and all(r.s == 201 for r in race_results), race_results[0] if race_results else None)
check("they all carry the same body", len(race_results) == 6 and all(r.b == race_results[0].b for r in race_results))
check("exactly one of them ran the change", sum(1 for r in race_results if not r.h.get("Idempotent-Replayed")) == 1)
r = call("GET", "/api/events/", params={"q": ctitle})
check("only one event was created", r.s == 200 and r.b["count"] == 1, r)

# password length on register
long_ok = "Zq7!rvTn#4Lw9dKp" * 8
r = call("POST", "/api/auth/register/", {"username": f"smoke_cap1_{STAMP}", "email": f"smoke_cap1_{STAMP}@example.com", "password": long_ok, "role": "attendee"})
check("a password of 128 characters is accepted", r.s == 201, r)
r = call("POST", "/api/auth/register/", {"username": f"smoke_cap2_{STAMP}", "email": f"smoke_cap2_{STAMP}@example.com", "password": long_ok + "x", "role": "attendee"})
expect("a password of 129 characters", r, 400, "validation_error", "password")

# sign-in lockout
lock = make_user("lock", "attendee")
wrong = {"username": lock["name"], "password": "Wrong-Pass-1x"}
five = [call("POST", "/api/auth/login/", wrong) for _ in range(5)]
check("five wrong passwords each answer 401", all(x.s == 401 and err(x).get("code") == "not_authenticated" for x in five), five[-1])
check("the five answers are identical", all(x.b == five[0].b for x in five))
r = call("POST", "/api/auth/login/", wrong)
expect("the sixth attempt is blocked", r, 429, "too_many_attempts")
retry = r.h.get("Retry-After")
check("Retry-After is whole seconds from 1 to 900", retry is not None and retry.isdigit() and 1 <= int(retry) <= 900, r)
check("the blocked answer is JSON", is_json(r), r)
expect("the correct password does not get in during a lockout", call("POST", "/api/auth/login/", {"username": lock["name"], "password": PASSWORD}), 429, "too_many_attempts")
expect("a missing password is a 400 even during a lockout", call("POST", "/api/auth/login/", {"username": lock["name"]}), 400, "validation_error")
check("a token from before the lockout still works", call("GET", "/api/auth/me/", token=lock["token"]).s == 200)
r = call("POST", "/api/auth/login/", {"username": att4["name"], "password": PASSWORD})
check("another username from the same address is not blocked", r.s == 200, r)
r = call("POST", "/api/auth/register/", {"username": f"smoke_afterlock_{STAMP}", "email": f"smoke_afterlock_{STAMP}@example.com", "password": PASSWORD, "role": "attendee"})
check("register is not limited during a lockout", r.s == 201, r)
r = call("POST", "/api/auth/login/", {"username": "smoke_" + "x" * 150, "password": "Wrong-Pass-1x"})
check("a username over 150 characters gives the same 401 as a wrong password", r.s == 401 and r.b == five[0].b, r)
r = call("POST", "/api/auth/login/", {"username": att2["name"], "password": "P" * 129})
check("a password over 128 characters gives the same 401 as a wrong password", r.s == 401 and r.b == five[0].b, r)

# body size
r = big_body("POST", "/api/events/", 1048577)
expect("a body over 1048576 bytes is refused before authentication", r, 413, "payload_too_large")
check("the 413 answer is JSON", is_json(r), r)
check("the 413 answer carries a request id", is_hex32(r.h.get("X-Request-ID")), r)
padded = "{bad" + " " * (1048576 - 4)
r = call("POST", "/api/auth/register/", raw=padded)
expect("a body of exactly 1048576 bytes is accepted", r, 400, "validation_error")

# ---------------------------------------------------------------- request id
print("... checking request ids and metrics")
r1 = call("GET", "/api/health/")
r2 = call("GET", "/api/health/")
check("an answer carries a request id of 32 lowercase hex characters", is_hex32(r1.h.get("X-Request-ID")), r1)
check("two requests get different request ids", r1.h.get("X-Request-ID") != r2.h.get("X-Request-ID"), r2)
r = call("GET", "/api/health/", extra={"X-Request-ID": f"smoke-{STAMP}_id"})
check("a valid request id from the client is kept", r.h.get("X-Request-ID") == f"smoke-{STAMP}_id", r)
r = call("GET", "/api/health/", extra={"X-Request-ID": "bad;chars"})
check("an invalid request id from the client is replaced", is_hex32(r.h.get("X-Request-ID")), r)
r = call("GET", f"/api/smoke-nothing-{STAMP}/")
check("the JSON not_found answer carries a request id", r.s == 404 and is_hex32(r.h.get("X-Request-ID")), r)
r = call("GET", "/api/auth/me/")
check("an error answer carries a request id", r.s == 401 and is_hex32(r.h.get("X-Request-ID")), r)

# ---------------------------------------------------------------- metrics
r = call("GET", "/metrics")
if r.s == 404:
    print("   /metrics is off on this server (METRICS_ENABLED is not true)")
    check("a disabled /metrics gives the JSON not_found", err(r).get("code") == "not_found" and is_json(r), r)
    check("a disabled /metrics gives the JSON not_found for POST too", call("POST", "/metrics", {}).s == 404)
elif r.s == 401:
    print("   /metrics needs a token on this server (METRICS_TOKEN is set)")
    check("/metrics without its token gives 401 not_authenticated", err(r).get("code") == "not_authenticated", r)
    r = call("GET", "/metrics", token="wrong-token")
    check("/metrics with a wrong token gives 401 not_authenticated", r.s == 401 and err(r).get("code") == "not_authenticated", r)
elif r.s == 200:
    print("   /metrics is on and open on this server")
    text = r.b if isinstance(r.b, str) else ""
    check("/metrics shows both metrics in the text format",
          "text/plain" in (r.h.get("Content-Type") or "") and "# TYPE http_requests_total counter" in text
          and "# TYPE http_request_duration_seconds histogram" in text, r)
    check("/metrics has counted the health checks under the route name", 'route="api/health/"' in text, r)
    check("/metrics does not count itself", 'route="metrics"' not in text, r)
    check("POST on /metrics is refused", call("POST", "/metrics", {}).s == 405)
else:
    check("/metrics answers 404, 401 or 200", False, r)

print("   Rate limits are checked apart: start the server with throttling on and run with --throttle-only.")

# ---------------------------------------------------------------- summary
finish()
