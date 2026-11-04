"""Deterministic AI safety controls: redaction, untrusted-input handling, output checks.

These reduce risk; they are not the security boundary. Authorization and tool execution
are decided by backend code (``app.auth``, ``app.tools.registry``), never by the model.
"""
