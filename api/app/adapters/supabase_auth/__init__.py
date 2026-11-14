"""Supabase Auth admin adapter for the AuthAdmin port (REST API over httpx, no vendor SDK)."""

from app.adapters.supabase_auth.admin import SupabaseAuthAdmin, from_settings

__all__ = ["SupabaseAuthAdmin", "from_settings"]
