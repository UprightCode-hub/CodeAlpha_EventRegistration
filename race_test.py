"""Last seat race test against the running server.

Start the server first (python manage.py runserver), then run this file from any
folder: python race_test.py. It uses only the standard library and creates its
own throwaway users and event, so it leaves the demo data alone.

Phase 1: two attendees fill two of three seats, then ten attendees register for
the last seat at the same moment.
Phase 2: the three registered attendees cancel at the same moment.
"""

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "http://127.0.0.1:8000"
PASSWORD = "Smoke-Test-Pass-91!"
PHONE = "+2348012345678"
PRE_FILLED = 2
RACERS = 10
CAPACITY = PRE_FILLED + 1

results = []


def check(name, ok, detail=""):
    """Prints one result line and remembers it for the summary."""
    results.append(bool(ok))
    suffix = f"  [{detail}]" if detail and not ok else ""
    print(("PASS " if ok else "FAIL ") + name + suffix)


def call(method, path, body=None, token=None):
    """Sends one JSON request and returns the status code and the decoded body."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = None
    return status, payload


def get_all(path, token=None):
    """Reads every page of a paged list and returns all rows in order."""
    rows = []
    page = 1
    while True:
        status, payload = call("GET", f"{path}?page={page}", token=token)
        if status != 200:
            raise RuntimeError(f"GET {path} page {page} answered {status}")
        rows.extend(payload["results"])
        if not payload["next"]:
            return rows
        page += 1


def make_user(username, role):
    """Registers a user through the API, signs in and returns the token."""
    status, _ = call(
        "POST",
        "/api/auth/register/",
        {"username": username, "email": f"{username}@example.com", "password": PASSWORD, "role": role},
    )
    if status != 201:
        raise RuntimeError(f"register {username} answered {status}")
    status, payload = call("POST", "/api/auth/login/", {"username": username, "password": PASSWORD})
    if status != 200:
        raise RuntimeError(f"login {username} answered {status}")
    return {"name": username, "token": payload["access"]}


def register(person, event_id):
    """Registers one attendee for the event."""
    return call(
        "POST",
        "/api/registrations/",
        {"event": event_id, "full_name": "Smoke Tester", "phone": PHONE},
        person["token"],
    )


def run_together(jobs):
    """Runs every job in its own thread, released at the same moment."""
    barrier = threading.Barrier(len(jobs))
    outcomes = [None] * len(jobs)

    def worker(index, job):
        barrier.wait()
        try:
            outcomes[index] = job()
        except Exception as error:  # a crashed thread must show up as a failed check
            outcomes[index] = (None, str(error))

    threads = [threading.Thread(target=worker, args=(i, job)) for i, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return outcomes


def notifications_of(person):
    """Returns all notifications of one attendee."""
    return get_all("/api/notifications/", person["token"])


def main():
    stamp = str(int(time.time()))
    try:
        organizer = make_user(f"smoke_org_{stamp}", "organizer")
    except urllib.error.URLError:
        print("The server does not answer at " + BASE + ". Start it with: python manage.py runserver")
        return 2
    people = [make_user(f"smoke_att_{stamp}_{i}", "attendee") for i in range(PRE_FILLED + RACERS)]
    start = datetime.now(timezone.utc) + timedelta(days=7)
    status, event = call(
        "POST",
        "/api/events/",
        {
            "title": f"Smoke race {stamp}",
            "description": "Throwaway event for the last seat race.",
            "category": "meetup",
            "event_type": "in_person",
            "venue_name": "Hall A",
            "address": "1 Main Street",
            "city": "Lagos",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=2)).isoformat(),
            "capacity": CAPACITY,
            "contact_email": "host@example.com",
            "contact_phone": PHONE,
        },
        organizer["token"],
    )
    check("event created with 3 seats", status == 201 and event["capacity"] == CAPACITY, f"{status} {event}")
    event_id = event["id"]

    # Phase 1: two seats filled one after the other, then ten people race for the last one.
    registrations = {}
    for person in people[:PRE_FILLED]:
        status, payload = register(person, event_id)
        check(f"{person['name']} registered for a free seat", status == 201 and payload["status"] == "registered", f"{status} {payload}")
        registrations[person["name"]] = payload["id"] if payload else None

    racers = people[PRE_FILLED:]
    outcomes = run_together([(lambda p=person: register(p, event_id)) for person in racers])
    codes = [status for status, _ in outcomes]
    check("no answer was a server error", all(code is not None and code < 500 for code in codes), codes)
    check("all ten racers got 201", all(code == 201 for code in codes), codes)
    statuses = [payload["status"] for code, payload in outcomes if code == 201]
    check("exactly 1 racer got the last seat", statuses.count("registered") == 1, statuses)
    check("the other 9 were waitlisted", statuses.count("waitlisted") == RACERS - 1, statuses)
    for person, (code, payload) in zip(racers, outcomes):
        if code == 201:
            registrations[person["name"]] = payload["id"]

    _, detail = call("GET", f"/api/events/{event_id}/")
    check("event shows 3 registered", detail["registered_count"] == CAPACITY, detail)
    check("event shows 0 seats left", detail["seats_left"] == 0, detail)
    check("event shows 9 on the waitlist", detail["waitlist_count"] == RACERS - 1, detail)

    rows = get_all(f"/api/events/{event_id}/registrations/", organizer["token"])
    states = [row["status"] for row in rows]
    check("organizer sees 12 registrations", len(rows) == PRE_FILLED + RACERS, len(rows))
    check("12 different attendees hold a row", len({row["attendee_username"] for row in rows}) == PRE_FILLED + RACERS)
    check("organizer list has 3 registered and 9 waitlisted", states.count("registered") == 3 and states.count("waitlisted") == 9, states)

    positions = []
    for person in racers:
        _, row = call("GET", f"/api/registrations/{registrations[person['name']]}/", token=person["token"])
        if row["status"] == "waitlisted":
            positions.append(row["waitlist_position"])
    check("waitlist positions are 1 to 9 with no gaps", sorted(positions) == list(range(1, RACERS)), sorted(positions))

    kinds = []
    for person in people:
        notes = notifications_of(person)
        kinds.append([note["kind"] for note in notes])
    check("every attendee has exactly one notification", all(len(item) == 1 for item in kinds), kinds)
    flat = [item[0] for item in kinds if item]
    check("3 confirmed and 9 waitlisted notifications", flat.count("registration_confirmed") == 3 and flat.count("waitlisted") == 9, flat)

    # Phase 2: the three seat holders cancel at the same moment.
    waiting_order = [row["id"] for row in rows if row["status"] == "waitlisted"]
    expected_promoted = set(waiting_order[:3])
    holders = [row for row in rows if row["status"] == "registered"]
    by_username = {person["name"]: person for person in people}
    outcomes = run_together(
        [
            (lambda r=row: call("POST", f"/api/registrations/{r['id']}/cancel/", token=by_username[r["attendee_username"]]["token"]))
            for row in holders
        ]
    )
    codes = [status for status, _ in outcomes]
    check("all three cancels answered 200", all(code == 200 for code in codes), codes)

    _, detail = call("GET", f"/api/events/{event_id}/")
    check("after the cancels 3 are registered", detail["registered_count"] == CAPACITY, detail)
    check("after the cancels 6 are waitlisted", detail["waitlist_count"] == RACERS - 4, detail)
    rows = get_all(f"/api/events/{event_id}/registrations/", organizer["token"])
    now_registered = {row["id"] for row in rows if row["status"] == "registered"}
    check("the 3 oldest waitlisted were promoted", now_registered == expected_promoted, sorted(now_registered))
    check("3 registrations are cancelled", [row["status"] for row in rows].count("cancelled") == 3)

    promoted_for = []
    quiet_ok = True
    holder_names = {row["attendee_username"] for row in holders}
    for person in people:
        notes = notifications_of(person)
        promoted_for.extend(note["registration"] for note in notes if note["kind"] == "promoted")
        if person["name"] in holder_names and len(notes) != 1:
            quiet_ok = False
    check("exactly 3 promoted notifications, for the right registrations", sorted(promoted_for) == sorted(expected_promoted), promoted_for)
    check("the attendees who cancelled got no new notification", quiet_ok)

    failed = results.count(False)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
