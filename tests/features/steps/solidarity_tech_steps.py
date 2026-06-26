# behave's @given/@when/@then are dynamically typed; Pylance (the CI pyright) resolves
# them to a _StepDecorator it treats as non-callable. Suppress that false positive here -
# every other pyright rule stays on for this file.
# pyright: reportCallIssue=false
from __future__ import annotations

import asyncio

import httpx
import respx
from behave import given, when, then

from rosadmin.membership.errors import DecodeError
from rosadmin.membership.solidarity_tech.client import SolidarityTechClient
from rosadmin.membership.solidarity_tech.fixtures import (
    status_prop,
    user_json,
    users_page,
)
from rosadmin.membership.source import Standing

BASE = "https://st.test/v1"


def _good(st_id, email="kris@example.com"):
    return user_json(
        st_id,
        email,
        {"membership-status": status_prop("Member in Good Standing")},
    )


def _retired(st_id, email="noelle@example.com"):
    return user_json(
        st_id,
        email,
        {"membership-status": status_prop("Lapsed Member")},
    )


def _run(coro):
    return asyncio.run(coro)


@given("a stubbed Solidarity Tech API with one good-standing member")
def step_one_good(context):
    context.router = respx.mock(base_url=BASE, assert_all_called=False)
    context.router.get("/users").mock(
        return_value=httpx.Response(200, json=users_page([_good(1)], 1, 100, 0))
    )
    context.router.start()


@given("a stubbed Solidarity Tech API with 150 good-standing members across two pages")
def step_two_pages(context):
    page1 = users_page([_good(i) for i in range(100)], 150, 100, 0)
    page2 = users_page([_good(i) for i in range(100, 150)], 150, 100, 100)
    context.router = respx.mock(base_url=BASE, assert_all_called=False)
    context.router.get("/users").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    context.router.start()


@given(
    "a stubbed Solidarity Tech API with one good-standing member and one retired-tier record"
)
def step_good_and_retired(context):
    page = users_page([_good(1), _retired(2)], 2, 100, 0)
    context.router = respx.mock(base_url=BASE, assert_all_called=False)
    context.router.get("/users").mock(return_value=httpx.Response(200, json=page))
    context.router.start()


@given("a stubbed Solidarity Tech API whose email lookup returns a retired-tier record")
def step_lookup_retired(context):
    page = users_page([_retired(2)], 1, 100, 0)
    context.router = respx.mock(base_url=BASE, assert_all_called=False)
    context.router.get("/users").mock(return_value=httpx.Response(200, json=page))
    context.router.start()


@when("Ralsei lists the members")
def step_list(context):
    client = SolidarityTechClient(token="t0ken", base_url=BASE)
    context.members = _run(client.list_members())
    _run(client.aclose())


@when('Ralsei looks up "{email}"')
def step_lookup(context, email):
    client = SolidarityTechClient(token="t0ken", base_url=BASE)
    try:
        context.result = _run(client.find_by_email(email))
        context.error = None
    except DecodeError as e:
        context.error = e
    finally:
        _run(client.aclose())


@then("the request carried the bearer token")
def step_bearer(context):
    request = context.router.calls.last.request
    assert request.headers["Authorization"] == "Bearer t0ken"


@then("one member in good standing is returned")
def step_one_member(context):
    assert len(context.members) == 1
    assert context.members[0].standing is Standing.GOOD_STANDING


@then("{count:d} members are returned")
def step_count(context, count):
    assert len(context.members) == count


@then("two pages were fetched")
def step_two_calls(context):
    assert len(context.router.calls) == 2


@then("the lookup fails with a decode error")
def step_lookup_error(context):
    assert isinstance(context.error, DecodeError)
