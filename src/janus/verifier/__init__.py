"""Deterministic postcondition checks: whether an executed step actually took
effect, and what the run's true status is -- never taken on a claimed DONE
status's word alone.
"""

from __future__ import annotations
