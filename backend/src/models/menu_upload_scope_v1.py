"""Persist the original Python scope interpretation without guessing malformed metadata."""


def scope_kind(metadata):
    try:
        value = dict(metadata or {})
        return "scoped" if bool(str(value.get("scope_override") or "").strip()) else "unscoped"
    except (TypeError, ValueError):
        return "invalid"


def scope_default(context):
    parameters = context.get_current_parameters()
    return scope_kind(parameters.get("metadata", parameters.get("metadata_json")))
