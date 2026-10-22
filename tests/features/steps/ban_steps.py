# behave's @given/@when/@then are dynamically typed; Pylance (the CI pyright) resolves
# them to a _StepDecorator it treats as non-callable. Suppress that false positive here -
# every other pyright rule stays on for this file.
# pyright: reportCallIssue=false
from __future__ import annotations

from behave import step

from tests.support.pg import ban


@step('"{email}" is banned')
def step_ban(context, email):
    ban(context.db.superuser_dsn, email)
