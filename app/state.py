"""
Shared in-memory state for passing call metadata between HTTP endpoints
and the WebSocket bridge.

In production, replace with Redis or a database.
"""

# Maps call_sid → call metadata (e.g. patient_name)
call_metadata: dict[str, dict] = {}
