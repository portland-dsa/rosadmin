"""The development-only surface: persona stubs and the fake-login door.

This package is installed only in development and staging - its absence in
production is the hard half of the double gate that keeps fake-login out of
production (the soft half is the fake-login setting). Everything here implements
interfaces owned by `rosadmin.web`.
"""

from rosadmin_devtools.fake_login import fake_login_router
from rosadmin_devtools.stubs import StubDirectory

__all__ = ["StubDirectory", "fake_login_router"]
