"""Small helper for the PG.3 live run: sign in to a deployed WhiteHats platform and call its API.

The password is read from the environment variable WH_PASSWORD (never from a file, never printed).
Usage: WH_PASSWORD=... python3 pg3_api.py <base url> <email> <METHOD> <path> [json body file]
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def call(base, token, method, path, body=None, timeout=300):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or "null")
    except urllib.error.HTTPError as err:
        text = err.read().decode()
        try:
            return err.code, json.loads(text)
        except ValueError:
            return err.code, text


def sign_in(base, email):
    status, found = call(base, None, "POST", "/api/auth/login", {"email": email, "password": os.environ["WH_PASSWORD"]})
    if status != 200 or found.get("step") != "done":
        raise SystemExit(f"sign in failed: {status} step={found.get('step') if isinstance(found, dict) else found}")
    return found["token"]


if __name__ == "__main__":
    base, email, method, path = sys.argv[1:5]
    body = json.loads(Path(sys.argv[5]).read_text()) if len(sys.argv) > 5 else None
    token = sign_in(base, email)
    status, found = call(base, token, method, path, body)
    print(status)
    print(json.dumps(found, indent=1)[:20000])
