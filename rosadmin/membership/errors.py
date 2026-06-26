"""The membership error tree - one base, two leaves that drive opposite handling."""

from __future__ import annotations


class MembershipError(Exception):
    """Base for every error the membership layer raises."""


class MalformedMember(MembershipError):
    """A record the strict decode cannot project at all (e.g. no email).

    A lenient roster sweep skips it; even a targeted lookup skips it, because
    there is nothing to return.
    """


class DecodeError(MembershipError):
    """A record whose backend value could not be decoded (e.g. an unrecognized
    membership status or a malformed date).

    A lenient sweep skips it, but a targeted lookup surfaces it - a bad value on
    a record someone asked for by name is a data problem worth reporting, not a
    member to silently drop.
    """
