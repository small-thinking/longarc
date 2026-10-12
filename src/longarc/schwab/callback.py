"""One-use HTTPS loopback listener for the Schwab OAuth callback."""

from __future__ import annotations

import math
import secrets
import ssl
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import TracebackType
from urllib.parse import parse_qs, urlsplit

from longarc.schwab.auth import OAuthSession, read_private_json
from longarc.schwab.client import SchwabError

_HEALTH = b"ready\n"
_SUCCESS = (
    b"<!doctype html><title>Schwab connected</title>"
    b"<p>Authorization complete. You may close this tab.</p>"
)
_FAILURE = (
    b"<!doctype html><title>Authorization failed</title>"
    b"<p>Authorization could not be completed. Return to the terminal.</p>"
)
_REJECTED = b"<!doctype html><title>Invalid callback</title><p>Invalid callback request.</p>"


class _QuietHTTPServer(HTTPServer):
    def handle_error(self, request: object, client_address: object) -> None:
        # The standard handler prints tracebacks for malformed requests.
        pass


class CallbackServer:
    """Serve a single verified callback on the registered loopback address."""

    def __init__(self, session: OAuthSession, certfile: Path, keyfile: Path) -> None:
        self._session = session
        self._certfile = certfile
        self._keyfile = keyfile
        redirect = urlsplit(session.config.redirect_uri)
        if redirect.scheme != "https" or redirect.hostname != "127.0.0.1":
            raise SchwabError("Callback must use HTTPS on 127.0.0.1.")
        self._port = redirect.port or 443
        self._authority = redirect.netloc
        self._callback_path = redirect.path or "/"
        if self._callback_path == "/health":
            raise SchwabError("The registered callback path cannot be /health.")
        self._httpd: HTTPServer | None = None
        self._handled = False
        self._failure: SchwabError | None = None

    @property
    def address(self) -> tuple[str, int]:
        return ("127.0.0.1", self._port)

    @property
    def ready_url(self) -> str:
        return f"https://{self._authority}/health"

    def __enter__(self) -> CallbackServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner._handle(self)

            def log_message(self, format: str, *args: object) -> None:
                pass

        try:
            server = _QuietHTTPServer(self.address, Handler)
            try:
                tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                tls.minimum_version = ssl.TLSVersion.TLSv1_2
                tls.load_cert_chain(self._certfile, self._keyfile)
                server.socket = tls.wrap_socket(server.socket, server_side=True)
            except (OSError, ssl.SSLError):
                server.server_close()
                raise
        except (OSError, ssl.SSLError):
            raise SchwabError("Could not start the local HTTPS callback listener.") from None
        self._httpd = server
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._httpd is not None:
            self._httpd.server_close()
            self._httpd = None

    def _reply(self, handler: BaseHTTPRequestHandler, status: int, body: bytes) -> None:
        try:
            handler.send_response(status)
            handler.send_header(
                "Content-Type",
                "text/plain; charset=utf-8" if body == _HEALTH else "text/html; charset=utf-8",
            )
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Referrer-Policy", "no-referrer")
            handler.send_header("Content-Security-Policy", "default-src 'none'")
            handler.end_headers()
            handler.wfile.write(body)
        except OSError:
            # Token exchange may already have succeeded when the browser closes.
            pass

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.headers.get_all("Host", []) != [self._authority]:
            self._reply(handler, 400, _REJECTED)
            return
        target = urlsplit(handler.path)
        if target.path == "/health" and not target.query and not target.fragment:
            self._reply(handler, 200, _HEALTH)
            return
        if target.path != self._callback_path or target.fragment:
            self._reply(handler, 404, _REJECTED)
            return
        try:
            pending = read_private_json(self._session.directory / "pending-auth.json")
            expected = pending.get("state")
            query = parse_qs(target.query, keep_blank_values=True)
            states = query.get("state", [])
            codes = query.get("code", [])
            valid_state = (
                isinstance(expected, str)
                and len(states) == 1
                and secrets.compare_digest(states[0], expected)
            )
            if not valid_state or ("error" not in query and (len(codes) != 1 or not codes[0])):
                self._reply(handler, 400, _REJECTED)
                return
            if "error" in query:
                self._failure = SchwabError("Schwab authorization was not granted.")
                self._reply(handler, 400, _FAILURE)
            else:
                # Build from the registered URI, never from the request's Host header.
                callback_url = self._session.config.redirect_uri + "?" + target.query
                self._session.finish(callback_url)
                self._reply(handler, 200, _SUCCESS)
        except SchwabError:
            self._failure = SchwabError("OAuth callback failed; authorize again.")
            self._reply(handler, 500, _FAILURE)
        except Exception:
            self._failure = SchwabError("OAuth callback failed; authorize again.")
            self._reply(handler, 500, _FAILURE)
        self._handled = True

    def run(self, timeout: float = 3600) -> None:
        """Block until one callback completes or the wait expires."""
        server = self._httpd
        if server is None:
            raise SchwabError("Start the callback listener before waiting.")
        if not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise ValueError("Callback wait timeout must be positive.")
        deadline = time.monotonic() + timeout
        try:
            while not self._handled:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SchwabError("Authorization timed out; start again.")
                server.timeout = min(1.0, remaining)
                server.handle_request()
        except KeyboardInterrupt:
            raise SchwabError("Authorization listener interrupted.") from None
        if self._failure is not None:
            raise self._failure
