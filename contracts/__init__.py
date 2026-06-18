"""Versioned contract spine for the X-ray assistant.

Each major version is a self-contained package (`contracts.v1`, future
`contracts.v2`). Always import from a pinned version, never re-export the
"latest" — explicit beats implicit when wire compatibility is the whole point.
"""
