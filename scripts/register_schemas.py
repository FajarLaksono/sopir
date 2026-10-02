#!/usr/bin/env python3
"""Register Avro schemas with Redpanda Schema Registry.

Idempotent: re-registering a byte-identical schema returns the existing schema
id rather than creating a new version, so running this on every
`docker compose up` is safe and a no-op once registered.

Uses urllib rather than `requests` so the script runs inside the sopir-base
image, which is what the schema-init service does. Adding `requests` would mean
adding it to requirements.txt and therefore to all six service images, for four
HTTP calls.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCHEMA_REGISTRY_URL = os.getenv("SCHEMA_REGISTRY_URL", "http://localhost:8081")
SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"

REGISTRY_CONTENT_TYPE = "application/vnd.schemaregistry.v1+json"

SUBJECT_MAPPING = {
    "sim_telemetry.avsc": "sopir.sim.telemetry.v1-value",
    "veh_can.avsc": "sopir.veh.can.v1-value",
    "veh_gnss.avsc": "sopir.veh.gnss.v1-value",
    "veh_events.avsc": "sopir.veh.events.v1-value",
    "dlq.avsc": "sopir.dlq.v1-value",
}

COMPATIBILITY = "BACKWARD"


class RegistryUnreachable(OSError):
    """The registry could not be contacted at all, as opposed to rejecting a request.

    Kept distinct from a non-2xx response because the two mean different things:
    a 409 is a deliberate answer from the registry (and is what a breaking
    schema change produces), whereas this is the registry being down or not yet
    listening.
    """


def request(
    method: str,
    url: str,
    body: dict | None = None,
    timeout: int = 10,
) -> tuple[int, str]:
    """Perform one registry call, returning (status_code, body_text).

    HTTP error responses are returned rather than raised: a 409 from the
    compatibility check is a result to report, not an exception to escape.
    Connection failures raise RegistryUnreachable.
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", REGISTRY_CONTENT_TYPE)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise RegistryUnreachable(str(exc.reason)) from exc


def wait_for_registry(url: str, timeout: int = 60) -> bool:
    """Wait for schema registry to be ready."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            status, _ = request("GET", f"{url}/subjects", timeout=5)
            if status == 200:
                return True
        except RegistryUnreachable:
            pass
        time.sleep(2)
    return False


def set_compatibility(url: str, subject: str, level: str) -> bool:
    """Set compatibility level for a subject."""
    try:
        status, _ = request(
            "PUT",
            f"{url}/config/{subject}",
            {"compatibility": level},
        )
        return status in (200, 201)
    except RegistryUnreachable as exc:
        print(f"  WARNING: Failed to set compatibility for {subject}: {exc}")
        return False


def register_schema(url: str, subject: str, schema_path: Path) -> bool:
    """Register a schema with the registry.

    A non-2xx response is the compatibility check rejecting the schema. The
    registry returns 409 for that, with the reason in the response body, so it
    is printed verbatim rather than summarised -- that text is the entire
    diagnostic when someone edits a .avsc and the pipeline stops starting.
    """
    try:
        schema_json = json.loads(schema_path.read_text(encoding="utf-8"))

        status, text = request(
            "POST",
            f"{url}/subjects/{subject}/versions",
            {"schema": json.dumps(schema_json), "schemaType": "AVRO"},
        )

        if status in (200, 201):
            version = json.loads(text).get("id")
            print(f"  Registered {subject} (schema ID: {version})")
            return True

        print(f"  FAILED {subject}: {status} - {text}")
        return False

    except RegistryUnreachable as exc:
        print(f"  ERROR registering {subject}: registry unreachable ({exc})")
        return False
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  ERROR registering {subject}: {exc}")
        return False


def main():
    print(f"Schema Registry URL: {SCHEMA_REGISTRY_URL}")
    print(f"Schemas directory: {SCHEMAS_DIR}")

    if not wait_for_registry(SCHEMA_REGISTRY_URL):
        print("ERROR: Schema Registry not ready after timeout")
        sys.exit(1)

    print("Schema Registry is ready")

    # Global default first, so a subject that has not been configured
    # individually still inherits BACKWARD rather than the registry default.
    try:
        status, _ = request(
            "PUT",
            f"{SCHEMA_REGISTRY_URL}/config",
            {"compatibility": COMPATIBILITY},
        )
        if status in (200, 201):
            print(f"Set global compatibility to {COMPATIBILITY}")
    except RegistryUnreachable as exc:
        print(f"WARNING: Could not set global compatibility: {exc}")

    all_ok = True
    for filename, subject in SUBJECT_MAPPING.items():
        schema_path = SCHEMAS_DIR / filename
        if not schema_path.exists():
            print(f"  MISSING: {schema_path}")
            all_ok = False
            continue

        # Per-subject compatibility, so tightening one subject later cannot be
        # silently widened by the global setting.
        set_compatibility(SCHEMA_REGISTRY_URL, subject, COMPATIBILITY)

        if not register_schema(SCHEMA_REGISTRY_URL, subject, schema_path):
            all_ok = False

    # List all registered subjects for verification
    try:
        status, text = request("GET", f"{SCHEMA_REGISTRY_URL}/subjects", timeout=5)
        if status == 200:
            print("\nRegistered subjects:")
            for subject in json.loads(text):
                print(f"  - {subject}")
    except (RegistryUnreachable, json.JSONDecodeError):
        pass

    if not all_ok:
        print("\nSome schemas failed to register")
        sys.exit(1)

    print("\nAll schemas registered successfully")
    sys.exit(0)


if __name__ == "__main__":
    main()
