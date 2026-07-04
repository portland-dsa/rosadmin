from __future__ import annotations

import os

from rosadmin.cli import _listen_target


def test_no_systemd_environment_means_no_fd():
    assert _listen_target({}) is None


def test_matching_pid_with_one_fd_yields_fd_three():
    env = {"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "1"}
    assert _listen_target(env) == 3


def test_foreign_pid_is_ignored():
    env = {"LISTEN_PID": "1", "LISTEN_FDS": "1"}
    assert _listen_target(env) is None


def test_garbage_fd_counts_are_ignored():
    env = {"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "zero"}
    assert _listen_target(env) is None
