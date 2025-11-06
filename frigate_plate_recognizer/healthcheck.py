"""Simple healthcheck HTTP server for container orchestration."""

from __future__ import annotations

import http.server
import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)


class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that responds to healthcheck requests."""

    # Class variable to hold the health check function
    health_check_fn: Callable[[], bool] | None = None

    def log_message(self, format: str, *args) -> None:
        """Suppress default logging from BaseHTTPRequestHandler."""
        pass

    def do_GET(self) -> None:
        """Handle GET requests for healthcheck."""
        if self.path == "/health":
            is_healthy = True
            if self.health_check_fn:
                try:
                    is_healthy = self.health_check_fn()
                except Exception as exc:
                    logger.error(f"Health check function raised exception: {exc}")
                    is_healthy = False

            if is_healthy:
                self.send_response(200)
                self.send_header("Content-type", "text/plain")
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(503)
                self.send_header("Content-type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Service Unavailable")
        else:
            self.send_response(404)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")


def start_healthcheck_server(
    port: int, health_check_fn: Callable[[], bool] | None = None
) -> threading.Thread:
    """
    Start a simple HTTP healthcheck server in a background thread.

    Args:
        port: Port number to listen on
        health_check_fn: Optional function that returns True if healthy, False otherwise

    Returns:
        The thread running the server
    """
    HealthCheckHandler.health_check_fn = health_check_fn

    server = http.server.HTTPServer(("", port), HealthCheckHandler)
    logger.info(f"Starting healthcheck server on port {port}")

    thread = threading.Thread(target=server.serve_forever, daemon=True, name="healthcheck")
    thread.start()

    return thread


__all__ = ["start_healthcheck_server"]
