"""Shared test setup: no test may use the network.

Every test runs with real connections switched off. A test that tries to open
one fails at once with NetworkAttempt. It derives from BaseException on
purpose: our code catches `Exception` to keep one failing firm from crashing a
run, and that must never be able to swallow this alarm.
"""

import socket

import pytest


class NetworkAttempt(BaseException):
    pass


def _refuse(*args, **kwargs):
    raise NetworkAttempt("a test tried to use the network; tests must run offline")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
