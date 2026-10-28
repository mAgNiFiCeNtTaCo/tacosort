"""Talking to real mail servers: provider presets, passwords/sign-in, and the IMAP backend."""

from .providers import PROVIDERS, Provider, detect_provider

__all__ = ["PROVIDERS", "ImapBackend", "Provider", "detect_provider"]


def __getattr__(name: str):
    # ImapBackend is loaded on first use, so importing providers doesn't need IMAPClient.
    if name == "ImapBackend":
        from .client import ImapBackend

        return ImapBackend
    raise AttributeError(name)
