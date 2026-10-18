"""Supabase is authoritative for synthetic work orders, feedback and sync outbox.

Typed ports live in adapters.feedback_contracts; the server-only REST/RPC
implementation lives in adapters.supabase_store. SQLite remains a labeled legacy
CLI snapshot and is never a persistence substitute in the Streamlit application.
"""
