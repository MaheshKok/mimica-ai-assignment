"""Top-level pytest configuration.

Intentionally empty. Shared fixtures live next to the tests that use
them (``tests/unit`` and ``tests/integration``); keeping this root
conftest bare avoids accidental import-time side effects for every
test run.
"""
