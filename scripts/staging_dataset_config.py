"""Validate staging-only snapshot settings and preserve existing deploy env args."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

KEYS = ("SAWA_STG_REPLAY_MANIFEST_URI", "SAWA_STG_REPLAY_MANIFEST_SHA256")


def configuration(environment):
    values = {key: environment.get(key, "") for key in KEYS}
    if not any(values.values()):
        return {}
    if not all(values.values()):
        raise ValueError("Both staging snapshot URI and SHA256 are required")
    uri = urlsplit(values[KEYS[0]])
    if (uri.scheme != "gs" or not re.search(r"(?:^|-)stg(?:-|$)", uri.netloc) or uri.query or uri.fragment
            or not re.fullmatch(r"gs://[a-z0-9.-]+/stg-replay/[A-Za-z0-9_./-]+\.json", values[KEYS[0]])
            or not uri.path.startswith("/stg-replay/") or any(part in {".", ".."} for part in unquote(uri.path).split("/"))
            or not re.fullmatch(r"[0-9a-f]{64}", values[KEYS[1]])):
        raise ValueError("An immutable staging snapshot URI and SHA256 are required")
    return values


def merge_args(args, values):
    result = list(args)
    if not values:
        return result
    if "--env-vars-file" in result:
        index = result.index("--env-vars-file") + 1
        path = Path(result[index])
        existing = json.loads(path.read_text())
        if not isinstance(existing, dict):
            raise ValueError("Runtime env file must be an object")
        path.write_text(json.dumps({**existing, **values}))
    elif "--set-env-vars" in result:
        index = result.index("--set-env-vars") + 1
        if result[index].startswith("^"):
            raise ValueError("Unsupported deployment environment delimiter")
        pairs = dict(item.split("=", 1) for item in result[index].split(",") if item)
        pairs.update(values)
        result[index] = ",".join(key + "=" + value for key, value in pairs.items())
    else:
        result.append("--update-env-vars=" + ",".join(key + "=" + value for key, value in values.items()))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("env", "argv"), required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    values = configuration(os.environ) if args.environment == "stg" else {}
    if args.mode == "env":
        print(",".join(key + "=" + value for key, value in values.items()))
    else:
        actual = args.args[1:] if args.args[:1] == ["--"] else args.args
        for value in merge_args(actual, values):
            sys.stdout.write(value + "\0")


if __name__ == "__main__":
    main()
