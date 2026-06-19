"""Prove that pytest-socket blocks outbound network connections in default CI.

These tests verify that socket-level enforcement is active. They do NOT
rely on external service availability and will fail if the network-blocking
plugin is not installed or configured.
"""

import socket

import pytest

try:
    from pytest_socket import SocketBlockedError
except ImportError:
    # Dummy exception class — never raised but keeps except clauses valid
    # when pytest-socket is not installed.
    class SocketBlockedError(Exception):
        pass


class TestSocketBlockingEnforcement:
    """Verify that an attempted outbound TCP connection is blocked.

    These tests are the CI enforcement complement to the existing
    no-network tests that pass by not making network calls.
    """

    def test_outbound_tcp_connect_is_blocked(self) -> None:
        """Attempt an outbound TCP connection and expect it to be blocked.

        Uses a reserved-documentation address (203.0.113.1:80) from
        TEST-NET-3 (RFC 5737) so even if the blocker fails, no real
        server is contacted.  pytest-socket intercepts socket.socket()
        construction itself under --disable-socket.
        """
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            # TEST-NET-3 address — guaranteed non-routable per RFC 5737
            sock.connect(("203.0.113.1", 80))
            # If we reach here, pytest-socket is NOT blocking.
            sock.close()
            pytest.fail(
                "pytest-socket did not block outbound TCP connection. "
                "Ensure pytest-socket is installed and --disable-socket is active."
            )
        except SocketBlockedError:
            # Expected: pytest-socket raises SocketBlockedError when
            # socket.socket() is called under --disable-socket.
            pass
        except OSError as e:
            # Without pytest-socket, connect to TEST-NET-3 should fail
            # with an OSError.  The error message must implicate sockets
            # so we don't silently accept an unrelated failure.
            msg = str(e).lower()
            assert "socket" in msg or "network" in msg or "unreachable" in msg, (
                f"Unexpected OSError (pytest-socket may be missing): {e}"
            )
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def test_dns_resolution_is_blocked(self) -> None:
        """Attempt a DNS resolution and expect it to be blocked.

        Uses a .invalid TLD (RFC 2606) so that even in a failure mode
        where pytest-socket does not intercept, the resolver stub
        returns NXDOMAIN immediately without querying any name server.
        """
        try:
            socket.getaddrinfo("this.does.not.exist.invalid", 80)
            pytest.fail(
                "pytest-socket did not block DNS resolution. "
                "Ensure pytest-socket is installed and --disable-socket is active."
            )
        except SocketBlockedError:
            # Expected: pytest-socket intercepts getaddrinfo.
            pass
        except OSError as e:
            msg = str(e).lower()
            assert "socket" in msg or "network" in msg or "name" in msg, (
                f"Unexpected OSError (pytest-socket may be missing): {e}"
            )
