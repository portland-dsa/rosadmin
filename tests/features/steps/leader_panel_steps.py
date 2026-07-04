# behave's @given/@when/@then are dynamically typed; Pylance (the CI pyright) resolves
# them to a _StepDecorator it treats as non-callable. Suppress that false positive here -
# every other pyright rule stays on for this file.
# pyright: reportCallIssue=false
from __future__ import annotations

from behave import given, then, when
from fastapi.testclient import TestClient

from rosadmin.service import create_app
from rosadmin.web.settings import WebSettings


def _client(*, fake_login_enabled: bool) -> TestClient:
    app = create_app(
        WebSettings(fake_login_enabled=fake_login_enabled, allowed_origin=None)
    )
    # https base_url so the Secure session cookie is carried; over http it is dropped.
    return TestClient(app, base_url="https://testserver")


def _groups(context) -> list[dict]:
    return context.client.get("/api/me/groups").json()


def _group_id(context, name: str) -> str:
    return next(g["id"] for g in _groups(context) if g["name"] == name)


def _members(context, name: str) -> list[dict]:
    return next(g["members"] for g in _groups(context) if g["name"] == name)


def _search_member_id(context, email: str) -> str:
    hit = context.client.post("/api/members/search", json={"email": email}).json()
    return hit["member"]["id"]


@given("the service is running with fake-login enabled")
def step_enabled(context):
    context.client = _client(fake_login_enabled=True)


@given("the service is running with fake-login disabled")
def step_disabled(context):
    context.client = _client(fake_login_enabled=False)


@given("Ralsei is logged in as the leader")
def step_login(context):
    context.login = context.client.post(
        "/api/auth/fake-login", json={"persona": "leader"}
    )
    assert context.login.status_code == 200, context.login.text


@then('the login response shows the display name "{name}"')
def step_login_name(context, name):
    assert context.login.json()["display_name"] == name


@then("the login response lists {count:d} groups")
def step_login_group_count(context, count):
    assert len(context.login.json()["groups"]) == count


@then("the client holds a session cookie")
def step_cookie(context):
    assert context.client.cookies.get("rosadmin_session") is not None


@when("Ralsei fetches his groups")
def step_fetch(context):
    context.response = context.client.get("/api/me/groups")


@then("he manages {count:d} groups")
def step_manage_count(context, count):
    assert len(context.response.json()) == count


@then('one of them has the body type "{body_type}"')
def step_body_type(context, body_type):
    assert any(g["body_type"] == body_type for g in context.response.json())


@when('Ralsei searches for "{email}"')
def step_search(context, email):
    context.response = context.client.post("/api/members/search", json={"email": email})
    body = context.response.json()
    context.found_member_id = (
        body["member"]["id"] if body.get("status") == "good_standing" else None
    )


@then('the search status is "{status}"')
def step_status(context, status):
    assert context.response.json()["status"] == status


@then("the response carries no member record")
def step_no_member(context):
    assert "member" not in context.response.json()


@when('Ralsei adds the found member to "{group}"')
def step_add(context, group):
    context.response = context.client.post(
        f"/api/groups/{_group_id(context, group)}/members",
        json={"member_id": context.found_member_id},
    )


@then('"{group}" lists "{email}" as a member')
def step_lists(context, group, email):
    assert any(m["email"] == email for m in _members(context, group))


@then("the change is refused as a conflict")
def step_conflict(context):
    assert context.response.status_code == 409
    assert context.response.json()["code"] == "already_member"


@when('Ralsei removes "{email}" from "{group}"')
def step_remove(context, email, group):
    context.response = context.client.delete(
        f"/api/groups/{_group_id(context, group)}/members/"
        f"{_search_member_id(context, email)}"
    )


@then('"{group}" no longer lists "{email}"')
def step_not_listed(context, group, email):
    assert all(m["email"] != email for m in _members(context, group))


@then("the removal is refused because they are not a member")
def step_not_member(context):
    assert context.response.status_code == 404
    assert context.response.json()["code"] == "not_a_member"


@when('Ralsei tries to add an unknown member to "{group}"')
def step_add_unknown(context, group):
    context.response = context.client.post(
        f"/api/groups/{_group_id(context, group)}/members",
        json={"member_id": "00000000-0000-0000-0000-000000000000"},
    )


@then("the add is refused because there is no such member")
def step_member_not_found(context):
    assert context.response.status_code == 404
    assert context.response.json()["code"] == "member_not_found"


@when("an unauthenticated client requests the session overview")
def step_unauth(context):
    context.response = context.client.get("/api/me")


@then("the request is refused as unauthenticated")
def step_refused_unauth(context):
    assert context.response.status_code == 401
    assert context.response.headers["content-type"].startswith(
        "application/problem+json"
    )
    assert context.response.json()["code"] == "not_authenticated"


@when('a client attempts fake-login as "{persona}"')
def step_attempt_login(context, persona):
    context.response = context.client.post(
        "/api/auth/fake-login", json={"persona": persona}
    )


@then("the login is refused as an unknown persona")
def step_unknown_persona(context):
    assert context.response.status_code == 404
    assert context.response.json()["code"] == "unknown_persona"


@then("the login is refused because the persona is not a chapter leader")
def step_not_leader(context):
    assert context.response.status_code == 403
    assert context.response.json()["code"] == "not_chapter_leader"


@then("the fake-login route is absent")
def step_route_absent(context):
    assert context.response.status_code == 404
    assert context.response.json()["code"] == "not_found"
