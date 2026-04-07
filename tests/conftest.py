"""
Pytest configuration and shared fixtures.
"""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: integration tests that make real network calls — run with: pytest -m live -s",
    )
