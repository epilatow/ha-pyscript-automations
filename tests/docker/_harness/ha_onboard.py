#!/usr/bin/env python3
# This is AI generated code
"""Complete Home Assistant onboarding via the REST API.

Run once after HA finishes its first boot. Creates an
owner user, sets core config, opts out of analytics,
then prints ``client_id<TAB>access_token<TAB>refresh_token``
on stdout so the pytest fixture can stash all three and
refresh the access token later without re-onboarding.

The access token is short-lived (~30min). The refresh
token lives for weeks. Callers exchange the refresh token
for a fresh access token via /auth/token at runtime.

Not a uv script; Python 3.11+ stdlib only, so it can run
from inside the HA container where uv is not available.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _request(
    method: str,
    url: str,
    *,
    data: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, bytes]:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _wait_for_ha(base_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_err: str = ""
    while time.monotonic() < deadline:
        try:
            code, _ = _request("GET", f"{base_url}/api/onboarding", timeout=5)
            if code in (200, 401):
                # 200: onboarding endpoint responding.
                # 401: HA up and auth-guarded (onboarding
                # already done, which is fine).
                return
        except urllib.error.URLError as e:
            last_err = str(e)
        except (ConnectionError, OSError) as e:
            last_err = str(e)
        time.sleep(1.0)
    msg = f"timed out waiting for HA at {base_url}: {last_err}"
    raise RuntimeError(msg)


def _onboarding_done(base_url: str) -> bool:
    code, body = _request("GET", f"{base_url}/api/onboarding")
    if code != 200:
        return True
    steps = json.loads(body)
    return all(step.get("done", False) for step in steps)


def onboard(
    base_url: str,
    *,
    username: str,
    password: str,
    name: str,
    language: str = "en",
    country: str = "US",
) -> tuple[str, str, str]:
    """Drive the onboarding flow.

    Returns ``(client_id, access_token, refresh_token)``.
    The caller can exchange ``refresh_token`` for a fresh
    ``access_token`` later via::

        POST {base_url}/auth/token
        Content-Type: application/x-www-form-urlencoded

        client_id=<client_id>&
        grant_type=refresh_token&
        refresh_token=<refresh_token>
    """
    # Step 1: create the owner user. Returns an auth code
    # that we then exchange for tokens on /auth/token.
    # HA requires client_id to match the OAuth client it
    # issues the auth code for; use the canonical frontend
    # URL (what the UI would send).
    client_id = f"{base_url.rstrip('/')}/"
    code, body = _request(
        "POST",
        f"{base_url}/api/onboarding/users",
        data={
            "client_id": client_id,
            "name": name,
            "username": username,
            "password": password,
            "language": language,
        },
    )
    if code != 200:
        msg = f"onboarding/users returned {code}: {body!r}"
        raise RuntimeError(msg)
    auth_code = json.loads(body)["auth_code"]

    # Step 2: exchange the auth code for a refresh token
    # and access token. The access token is scoped to an
    # expiry; the refresh token is long-lived (weeks by
    # default) and is what the fixture stashes. client_id
    # must match the one used on /api/onboarding/users.
    import urllib.parse

    form = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": auth_code,
        }
    )
    tok_req = urllib.request.Request(
        f"{base_url}/auth/token",
        data=form.encode(),
        method="POST",
    )
    tok_req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(tok_req, timeout=30) as resp:
        tok_body = resp.read()
    tokens = json.loads(tok_body)
    access_token: str = tokens["access_token"]
    refresh_token: str = tokens["refresh_token"]
    bearer = {"Authorization": f"Bearer {access_token}"}

    # Step 3: complete remaining onboarding steps. HA
    # ignores unknown ones and errors on already-done
    # ones (which is fine if we race with the user);
    # loop over what /api/onboarding reports.
    code, body = _request(
        "GET",
        f"{base_url}/api/onboarding",
        headers=bearer,
    )
    remaining = [s["step"] for s in json.loads(body) if not s["done"]]

    for step in remaining:
        if step == "user":
            continue  # already done above
        payload: dict[str, object] = {}
        if step == "core_config":
            payload = {
                "country": country,
                "currency": "USD",
                "language": language,
                "time_zone": "UTC",
                "unit_system": "metric",
            }
        elif step == "analytics":
            payload = {"preferences": {}}
        # integration: empty payload acceptable.
        _request(
            "POST",
            f"{base_url}/api/onboarding/{step}",
            data=payload,
            headers=bearer,
        )

    return client_id, access_token, refresh_token


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Complete HA onboarding and print "
            "'client_id<TAB>access_token<TAB>refresh_token' on stdout."
        )
    )
    p.add_argument("--base-url", default="http://localhost:8123")
    p.add_argument("--username", default="testuser")
    p.add_argument("--password", default="testpassword123")
    p.add_argument("--name", default="Test User")
    p.add_argument("--wait-timeout", type=float, default=120.0)
    args = p.parse_args()

    _wait_for_ha(args.base_url, args.wait_timeout)

    if _onboarding_done(args.base_url):
        # Re-onboarding is not supported; caller should
        # tear down /config before retrying.
        print("onboarding already done", file=sys.stderr)
        return 1

    client_id, access_token, refresh_token = onboard(
        args.base_url,
        username=args.username,
        password=args.password,
        name=args.name,
    )
    print(f"{client_id}\t{access_token}\t{refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
