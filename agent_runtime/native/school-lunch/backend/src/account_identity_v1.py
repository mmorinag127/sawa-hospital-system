"""Versioned Python account identity used by the role lookup and migration."""


def account_identity(value):
    return str(value or "").strip().lower()
