"""Tests for http_client module."""

from __future__ import annotations

from frigate_plate_recognizer.http_client import (
    build_retry_strategy,
    build_session,
)


def test_build_retry_strategy():
    """Test retry strategy is configured correctly."""
    retry = build_retry_strategy(retries=5)
    
    assert retry.total == 5
    assert retry.read == 5
    assert retry.connect == 5
    assert retry.backoff_factor == 0.5
    assert retry.status_forcelist == (429, 500, 502, 503, 504)
    assert retry.respect_retry_after_header is True
    assert retry.raise_on_status is False


def test_build_session_with_timeout():
    """Test session is built with timeout."""
    session = build_session(timeout=30.0, retries=3)
    
    # Check adapters are mounted
    assert "http://" in session.adapters
    assert "https://" in session.adapters
    
    # Check timeout is configured
    http_adapter = session.adapters["http://"]
    assert hasattr(http_adapter, "_timeout")
    assert http_adapter._timeout == 30.0


def test_build_session_with_verify_true():
    """Test session respects verify=True."""
    session = build_session(timeout=10.0, retries=2, verify=True)
    assert session.verify is True


def test_build_session_with_verify_false():
    """Test session respects verify=False."""
    session = build_session(timeout=10.0, retries=2, verify=False)
    assert session.verify is False


def test_build_session_default_verify():
    """Test session uses default verify when not specified."""
    session = build_session(timeout=10.0, retries=2)
    # Default requests.Session verify is True
    assert session.verify is True
