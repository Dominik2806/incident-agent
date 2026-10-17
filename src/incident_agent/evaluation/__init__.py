"""Deterministic scoring of incident reports against the expected result of a scenario.

No model is called here: the scorer reads a finished report (Markdown) and checks it with plain
Python. Running the agent to produce reports is a separate, local step.
"""
