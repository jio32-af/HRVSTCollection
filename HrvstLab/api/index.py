"""Vercel serverless entrypoint: re-exports the Flask WSGI `app` from MP.py.

Vercel imports this module (never runs it as __main__), so MP's local
WSGI loop stays dormant and the platform serves `app` itself.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from MP import app  # noqa: E402  (re-exported for the Vercel Python runtime)
