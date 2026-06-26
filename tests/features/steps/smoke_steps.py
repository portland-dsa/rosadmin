# behave's @given/@then are dynamically typed; Pylance (the CI pyright) resolves them to
# a _StepDecorator it treats as non-callable. Suppress that false positive here.
# pyright: reportCallIssue=false
from behave import given, then


@given("the harness is set up")
def step_set_up(context):
    context.ready = True


@then("it reports ready")
def step_ready(context):
    assert context.ready is True
