"""Inspect Python CA stores and test certificate rejection without sending credentials."""

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import ssl
import urllib.error
import urllib.request


BLOCKED_ROOTS = {
    "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b",
    "d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4",
    "7dd78d5f4f13459a83dff9abbba62edbaf6f2d102bf257fd712f4d9f2746ed8d",
    "f2edaaff3d9b701fb4d14c9f285f8c0bad90fc3bcc1fa07d47f4718edaba34a5",
}
PROBES = (
    "https://dv-g3b-emudhra.we-have-a-problem.com/",
    "https://dv-g3b-aaa-emudhra.we-have-a-problem.com/",
)


def inspect_store(label, context, online):
    certs = context.get_ca_certs(binary_form=True)
    blocked = BLOCKED_ROOTS.intersection(hashlib.sha256(c).hexdigest() for c in certs)
    if not certs or blocked:
        raise RuntimeError(f"{label}: empty or unsafe trust store: {sorted(blocked)}")
    if online:
        with urllib.request.urlopen("https://pypi.org/", context=context, timeout=20) as response:
            if response.status != 200:
                raise RuntimeError(f"{label}: positive control failed")
        for url in PROBES:
            try:
                with urllib.request.urlopen(url, context=context, timeout=20):
                    pass
            except urllib.error.URLError as exc:
                if not isinstance(exc.reason, ssl.SSLCertVerificationError):
                    raise
            else:
                raise RuntimeError(f"{label}: compromised chain accepted: {url}")
    print(json.dumps({"store": label, "certificates": len(certs), "blocked_roots": 0, "online": online}))


def verify_grpc_roots():
    expected = "/etc/ssl/certs/ca-certificates.crt"
    configured = os.environ.get("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH")
    if configured != expected:
        raise RuntimeError(f"grpc roots path must be {expected}, got {configured!r}")
    if not Path(configured).is_file():
        raise RuntimeError(f"grpc roots path is not a file: {configured}")
    inspect_store("grpc-os-roots", ssl.create_default_context(cafile=configured), False)
    if importlib.util.find_spec("grpc") is not None:
        importlib.import_module("grpc")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--cafile")
    args = parser.parse_args()
    if args.cafile:
        inspect_store(args.cafile, ssl.create_default_context(cafile=args.cafile), args.online)
        return
    verify_grpc_roots()
    inspect_store("openssl", ssl.create_default_context(), args.online)
    for name in ("certifi", "pip._vendor.certifi"):
        bundle = importlib.import_module(name)
        inspect_store(name, ssl.create_default_context(cafile=bundle.where()), args.online)


if __name__ == "__main__":
    main()
