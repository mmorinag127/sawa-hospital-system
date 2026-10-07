"""Exact GCS object identity; URI decoration never selects another generation."""
import re
from typing import NamedTuple
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit


class GcsUri(NamedTuple):
    bucket: str
    object_name: str
    generation: str | None
    query: str
    fragment: str


def validate_generation(value) -> str:
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ValueError("storage_generation_invalid")
    return str(value)


def _query_pairs(query: str) -> list[tuple[str, str]]:
    if re.search(r"%(?![0-9A-Fa-f]{2})", query):
        raise ValueError("storage_query_invalid")
    return parse_qsl(query, keep_blank_values=True, encoding="utf-8", errors="strict")


def format_gcs_uri(bucket: str, object_name: str, generation=None) -> str:
    if not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", bucket):
        raise ValueError("storage_bucket_invalid")
    if not isinstance(object_name, str) or not object_name:
        raise ValueError("storage_object_required")
    query = "" if generation is None else "generation=" + validate_generation(generation)
    return urlunsplit(("gs", bucket, "/" + quote(object_name, safe="/"), query, ""))


def parse_gcs_uri(uri: str) -> GcsUri:
    if not isinstance(uri, str) or any(ord(char) < 32 or ord(char) == 127 for char in uri):
        raise ValueError("storage_uri_invalid")
    parsed = urlsplit(uri)
    if parsed.scheme != "gs" or not parsed.path.startswith("/"):
        raise ValueError("storage_uri_requires_gcs")
    encoded = parsed.path[1:]
    if re.search(r"%(?![0-9A-Fa-f]{2})", encoded):
        raise ValueError("storage_object_encoding_invalid")
    name = unquote(encoded, encoding="utf-8", errors="strict")
    # There is exactly one wire encoding. Literal '%' is always '%25'.
    canonical = format_gcs_uri(parsed.netloc, name)
    expected = urlsplit(canonical).path
    escaped = re.sub(r"%[0-9a-fA-F]{2}", lambda match: match[0].upper(), parsed.path)
    if escaped != expected:
        raise ValueError("storage_object_encoding_noncanonical")
    generations = [value for key, value in _query_pairs(parsed.query) if key == "generation"]
    if len(generations) > 1:
        raise ValueError("storage_generation_ambiguous")
    generation = validate_generation(generations[0]) if generations else None
    return GcsUri(parsed.netloc, name, generation, parsed.query, parsed.fragment)


def validate_source_query(query: str) -> None:
    if any(key == "generation" or key.lower().startswith("x-goog-") for key, value in _query_pairs(query)):
        raise ValueError("figure_source_query_conflicts_with_storage_identity")


def decorate_gcs_uri(uri: str, source_reference: str) -> str:
    storage = parse_gcs_uri(uri)
    source = urlsplit(source_reference)
    if source.scheme or source.netloc:
        raise ValueError("figure_source_requires_local_reference")
    validate_source_query(source.query)
    if storage.fragment:
        raise ValueError("uploaded_storage_uri_has_fragment")
    query = storage.query + ("&" if storage.query and source.query else "") + source.query
    return urlunsplit(("gs", storage.bucket, urlsplit(uri).path, query, source.fragment))


def signing_query(uri: GcsUri) -> dict[str, str]:
    result = {}
    for key, value in _query_pairs(uri.query):
        if key == "generation":
            continue
        if key in result or key.lower().startswith("x-goog-"):
            raise ValueError("storage_signing_query_ambiguous")
        result[key] = value
    return result
