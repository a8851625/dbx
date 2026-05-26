#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


def request_json(url: str, *, method: str = "GET") -> dict[str, Any]:
    request = Request(url, method=method, headers={"Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def wait_for_json(
    label: str,
    url: str,
    *,
    method: str = "GET",
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return request_json(url, method=method)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            last_error = exc
            time.sleep(poll_interval_seconds)
    raise SystemExit(f"{label} was not ready before timeout: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-check the compose OIDC stack for DBX local or shared test environments."
    )
    parser.add_argument("--app-base-url", default="http://localhost:4224")
    parser.add_argument("--issuer", default="http://localhost:8080/realms/dbx")
    parser.add_argument(
        "--authorize-prefix",
        default="http://localhost:8080/realms/dbx/protocol/openid-connect/auth",
    )
    parser.add_argument("--provider-name", default="DBX Local Keycloak")
    parser.add_argument("--client-id", default="dbx-web")
    parser.add_argument("--redirect-uri", default="http://localhost:4224/api/v1/auth/callback")
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    args = parser.parse_args()

    health = wait_for_json(
        "DBX health endpoint",
        f"{args.app_base_url}/health",
        timeout_seconds=args.timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    if health.get("status") != "ok":
        raise SystemExit(f"Unexpected DBX health payload: {health}")

    discovery = wait_for_json(
        "OIDC discovery document",
        f"{args.issuer}/.well-known/openid-configuration",
        timeout_seconds=args.timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    if discovery.get("issuer") != args.issuer:
        raise SystemExit(f"Unexpected OIDC issuer: {discovery}")

    auth_status = wait_for_json(
        "DBX auth status",
        f"{args.app_base_url}/api/v1/auth/status",
        timeout_seconds=args.timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    if auth_status.get("required") is not True:
        raise SystemExit(f"OIDC should be required in compose mode: {auth_status}")
    if auth_status.get("mock_mode") is not False:
        raise SystemExit(f"Mock mode should be disabled in compose mode: {auth_status}")
    if auth_status.get("provider_name") != args.provider_name:
        raise SystemExit(f"Unexpected provider name: {auth_status}")

    login_payload = request_json(f"{args.app_base_url}/api/v1/auth/login", method="POST")
    authorization_url = str(login_payload.get("authorization_url") or "")
    if not authorization_url.startswith(args.authorize_prefix):
        raise SystemExit(f"Unexpected authorization URL: {login_payload}")

    query = parse_qs(urlparse(authorization_url).query)
    if query.get("client_id") != [args.client_id]:
        raise SystemExit(f"Unexpected OIDC client_id in login redirect: {query}")
    if query.get("redirect_uri") != [args.redirect_uri]:
        raise SystemExit(f"Unexpected OIDC redirect_uri in login redirect: {query}")
    scopes = set((query.get("scope") or [""])[0].split())
    if "openid" not in scopes:
        raise SystemExit(f"OIDC login redirect is missing the openid scope: {query}")

    print("DBX compose OIDC smoke check passed.")
    print(f"DBX base URL: {args.app_base_url}")
    print(f"OIDC issuer: {args.issuer}")
    print("Test user: dbx-admin / dbx-admin-123 (admin@example.com)")


if __name__ == "__main__":
    main()
