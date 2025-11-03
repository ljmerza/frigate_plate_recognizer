"""Prometheus metrics constants for Frigate plate recognizer."""

from __future__ import annotations

import prometheus_client

on_connect_counter = prometheus_client.Counter('on_connect', 'count of connects')
on_disconnect_counter = prometheus_client.Counter('on_disconnect', 'count of connects')
mqtt_sends_counter = prometheus_client.Counter('mqtt_sends', 'count of sends', ['watched'])

code_project_counter = prometheus_client.Counter('code_project_calls', 'count of sends')
plate_recognizer_counter = prometheus_client.Counter('plate_recognizer_calls', 'count of sends')
plate_recognizer_err = prometheus_client.Counter('plate_recognizer_errors', 'count of sends')

http_request_latency_histogram = prometheus_client.Histogram(
    'http_request_duration_seconds',
    'HTTP request duration',
    ['service', 'operation'],
)

current_events_gauge = prometheus_client.Gauge(
    'current_events_tracked',
    'Number of events currently tracked',
)

processed_events_counter = prometheus_client.Counter(
    'processed_events_total',
    'Number of events processed categorised by result',
    ['result'],
)

db_writes_counter = prometheus_client.Counter(
    'db_writes_total',
    'Database write operations',
    ['status'],
)

db_errors_counter = prometheus_client.Counter(
    'db_errors_total',
    'Database errors',
    ['operation'],
)

__all__ = [
    'on_connect_counter',
    'on_disconnect_counter',
    'mqtt_sends_counter',
    'code_project_counter',
    'plate_recognizer_counter',
    'plate_recognizer_err',
    'http_request_latency_histogram',
    'current_events_gauge',
    'processed_events_counter',
    'db_writes_counter',
    'db_errors_counter',
]
