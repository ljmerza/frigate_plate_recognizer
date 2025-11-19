"""Tests for healthcheck module."""

from __future__ import annotations

import time
from http.client import HTTPConnection

from frigate_plate_recognizer.healthcheck import start_healthcheck_server


def test_healthcheck_returns_200_by_default():
    """Test healthcheck endpoint returns 200 OK when no check function provided."""
    # Start server on random available port
    port = 19999
    start_healthcheck_server(port)
    
    # Give server time to start
    time.sleep(0.5)
    
    # Make request
    conn = HTTPConnection("localhost", port, timeout=2)
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        assert response.status == 200
        body = response.read().decode()
        assert body == "OK"
    finally:
        conn.close()


def test_healthcheck_returns_200_when_healthy():
    """Test healthcheck endpoint returns 200 when check function returns True."""
    port = 19998
    
    def always_healthy() -> bool:
        return True
    
    start_healthcheck_server(port, health_check_fn=always_healthy)
    time.sleep(0.5)
    
    conn = HTTPConnection("localhost", port, timeout=2)
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        assert response.status == 200
        body = response.read().decode()
        assert body == "OK"
    finally:
        conn.close()


def test_healthcheck_returns_503_when_unhealthy():
    """Test healthcheck endpoint returns 503 when check function returns False."""
    port = 19997
    
    def always_unhealthy() -> bool:
        return False
    
    start_healthcheck_server(port, health_check_fn=always_unhealthy)
    time.sleep(0.5)
    
    conn = HTTPConnection("localhost", port, timeout=2)
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        assert response.status == 503
        body = response.read().decode()
        assert body == "Service Unavailable"
    finally:
        conn.close()


def test_healthcheck_returns_503_when_check_raises():
    """Test healthcheck endpoint returns 503 when check function raises exception."""
    port = 19996
    
    def raising_check() -> bool:
        raise RuntimeError("Something went wrong")
    
    start_healthcheck_server(port, health_check_fn=raising_check)
    time.sleep(0.5)
    
    conn = HTTPConnection("localhost", port, timeout=2)
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        assert response.status == 503
        body = response.read().decode()
        assert body == "Service Unavailable"
    finally:
        conn.close()


def test_healthcheck_returns_404_for_unknown_path():
    """Test healthcheck endpoint returns 404 for non-health paths."""
    port = 19995
    start_healthcheck_server(port)
    time.sleep(0.5)
    
    conn = HTTPConnection("localhost", port, timeout=2)
    try:
        conn.request("GET", "/unknown")
        response = conn.getresponse()
        assert response.status == 404
        body = response.read().decode()
        assert body == "Not Found"
    finally:
        conn.close()
