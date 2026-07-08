# behave's @given/@when/@then are dynamically typed; Pylance (the CI pyright) resolves
# them to a _StepDecorator it treats as non-callable. Suppress that false positive here -
# every other pyright rule stays on for this file.
# pyright: reportCallIssue=false
from __future__ import annotations

import asyncio

import httpx
import psycopg
from behave import given, then, when

from rosadmin.db import make_pool
from rosadmin.db.mutations import claim_and_add_member
from rosadmin.db.roster import PullReport, pull_roster
from rosadmin.membership.solidarity_tech.client import SolidarityTechClient
from rosadmin.membership.source import LeadershipAssessment
from rosadmin.mock_st.roster import parse_map
from rosadmin.mock_st.server import create_app

BASE = "http://mock.test"


def _read_members(app):
    async def go():
        transport = httpx.ASGITransport(app=app)
        http = httpx.AsyncClient(transport=transport, base_url=BASE)
        client = SolidarityTechClient(token="t0ken", base_url=BASE, client=http)
        try:
            return await client.list_members()
        finally:
            await client.aclose()

    return asyncio.run(go())


def _pull(app_dsn, members) -> PullReport:
    async def go():
        pool = make_pool(app_dsn)
        await pool.open()
        try:
            return await pull_roster(pool, members)
        finally:
            await pool.close()

    return asyncio.run(go())


def _query(dsn, sql_text, params=()):
    async def go():
        pool = make_pool(dsn)
        await pool.open()
        try:
            async with pool.connection() as conn:
                cursor = await conn.execute(sql_text, params)
                return await cursor.fetchall()
        finally:
            await pool.close()

    return asyncio.run(go())


_LEADER_ROWS_QUERY = (
    "SELECT lb.name FROM body_memberships bm "
    "JOIN members m ON m.id = bm.member_id "
    "JOIN leadership_bodies lb ON lb.id = bm.body_id "
    "WHERE m.email = %s AND bm.role = 'leader'"
)


@given('the persona roster "{spec}"')
def step_roster(context, spec):
    context.persona_spec = spec
    context.app = create_app(parse_map(spec))


@when("Ralsei pulls the roster")
def step_pull(context):
    members = _read_members(context.app)
    context.report = _pull(context.db.app_dsn, members)


@then("the members table holds {count:d} member")
@then("the members table holds {count:d} members")
def step_member_count(context, count):
    ((n,),) = _query(context.db.app_dsn, "SELECT count(*) FROM members")
    assert n == count


@then("the leadership_bodies table holds {count:d} body")
@then("the leadership_bodies table holds {count:d} bodies")
def step_body_count(context, count):
    ((n,),) = _query(context.db.app_dsn, "SELECT count(*) FROM leadership_bodies")
    assert n == count


@then('"{email}" leads "{body}"')
def step_leads(context, email, body):
    rows = _query(context.db.app_dsn, _LEADER_ROWS_QUERY, (email,))
    assert rows == [(body,)]


@then('"{email}" leads no bodies')
def step_leads_none(context, email):
    rows = _query(context.db.app_dsn, _LEADER_ROWS_QUERY, (email,))
    assert rows == []


@then('"{email}" is stored as {assessment_name}')
def step_assessment(context, email, assessment_name):
    ((assessment,),) = _query(
        context.db.app_dsn,
        "SELECT leadership_assessment FROM members WHERE email = %s",
        (email,),
    )
    assert assessment is LeadershipAssessment[assessment_name]


@then("the pull touched {count:d} member")
@then("the pull touched {count:d} members")
def step_report_members(context, count):
    assert context.report.members_upserted == count


@then("the pull touched {count:d} leadership body")
@then("the pull touched {count:d} leadership bodies")
def step_report_bodies(context, count):
    assert context.report.bodies_upserted == count


@then("the pull touched {count:d} leader row")
@then("the pull touched {count:d} leader rows")
def step_report_leader_rows(context, count):
    assert context.report.leader_rows == count


@then('the pull flags "{email}" as an anomaly')
def step_anomaly(context, email):
    ((member_id,),) = _query(
        context.db.app_dsn, "SELECT id FROM members WHERE email = %s", (email,)
    )
    assert any(a.member_id == member_id for a in context.report.anomalies)


def _id_by_email(dsn, email):
    ((member_id,),) = _query(dsn, "SELECT id FROM members WHERE email = %s", (email,))
    return member_id


def _id_by_body_name(dsn, name):
    ((body_id,),) = _query(
        dsn, "SELECT id FROM leadership_bodies WHERE name = %s", (name,)
    )
    return body_id


def _manual_add(app_dsn, *, member_email, body_name, added_by_email):
    member_id = _id_by_email(app_dsn, member_email)
    body_id = _id_by_body_name(app_dsn, body_name)
    added_by = _id_by_email(app_dsn, added_by_email)

    async def go():
        pool = make_pool(app_dsn)
        await pool.open()
        try:
            return await claim_and_add_member(
                pool, body_id=body_id, member_id=member_id, added_by=added_by
            )
        finally:
            await pool.close()

    return asyncio.run(go())


@when('Ralsei manually adds "{email}" to "{body}"')
def step_manual_add(context, email, body):
    outcome = _manual_add(
        context.db.app_dsn,
        member_email=email,
        body_name=body,
        added_by_email="ralsei@example.com",
    )
    assert outcome is not None


@when('Susie deletes "{email}"\'s member record outright')
def step_delete_member(context, email):
    # No pull path deletes a member row today; this is a direct DELETE, standing
    # in for a future deprovision sweep, to pin what a manual add's attribution
    # does when the adding leader's own record disappears.
    with psycopg.connect(context.db.superuser_dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM members WHERE email = %s", (email,))


@then('"{email}" is still a member of "{body}"')
def step_still_member(context, email, body):
    rows = _query(
        context.db.app_dsn,
        "SELECT 1 FROM body_memberships bm "
        "JOIN members m ON m.id = bm.member_id "
        "JOIN leadership_bodies lb ON lb.id = bm.body_id "
        "WHERE m.email = %s AND lb.name = %s AND bm.role = 'member'",
        (email, body),
    )
    assert len(rows) == 1, rows


@then(
    'the manual-add attribution for "{email}" in "{body}" is cleared '
    "but the timestamp remains"
)
def step_attribution_cleared(context, email, body):
    rows = _query(
        context.db.app_dsn,
        "SELECT bm.added_by, bm.manually_added_at FROM body_memberships bm "
        "JOIN members m ON m.id = bm.member_id "
        "JOIN leadership_bodies lb ON lb.id = bm.body_id "
        "WHERE m.email = %s AND lb.name = %s AND bm.role = 'member'",
        (email, body),
    )
    assert len(rows) == 1, rows
    added_by, manually_added_at = rows[0]
    assert added_by is None
    assert manually_added_at is not None


@then('the promoted row for "{email}" in "{body}" carries no manual-add provenance')
def step_promotion_clears_provenance(context, email, body):
    # A promotion hands the row to the records: the role flips to leader and
    # the manual-add provenance (who, when) is shed entirely - unlike the
    # adding leader's record disappearing, which keeps the timestamp.
    rows = _query(
        context.db.app_dsn,
        "SELECT bm.added_by, bm.manually_added_at FROM body_memberships bm "
        "JOIN members m ON m.id = bm.member_id "
        "JOIN leadership_bodies lb ON lb.id = bm.body_id "
        "WHERE m.email = %s AND lb.name = %s AND bm.role = 'leader'",
        (email, body),
    )
    assert rows == [(None, None)], rows
