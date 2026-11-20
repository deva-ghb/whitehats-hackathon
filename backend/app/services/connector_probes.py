"""Read-only connectivity checks for repository and endpoint providers."""
from urllib.parse import quote

import httpx

from ..security import unseal

FALCON = {
    "us-1": "https://api.crowdstrike.com",
    "us-2": "https://api.us-2.crowdstrike.com",
    "eu-1": "https://api.eu-1.crowdstrike.com",
}
KEYS = frozenset({"bitbucket", "crowdstrike"})


def check(db, connector):
    secret = unseal(connector.secret_enc, db, connector.org_id)
    if not secret:
        return "error", "Enter the connector credential."
    try:
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            if connector.key == "bitbucket":
                workspace = connector.config.get("workspace", "")
                if not workspace or any(not (char.isalnum() or char in "-_") for char in workspace):
                    return "error", "Enter a valid Bitbucket workspace slug."
                url = f"https://api.bitbucket.org/2.0/repositories/{quote(workspace, safe='')}"
                response = client.get(url, params={"pagelen": 1}, headers={"Authorization": f"Bearer {secret}"})
                response.raise_for_status()
                count = len(response.json()["values"])
                return "checked", f"Repository listing verified. Read {count} repository in the first page."
            base = FALCON.get(connector.config.get("region", "").lower())
            if not base:
                return "error", "Choose Falcon region us-1, us-2 or eu-1."
            auth = client.post(base + "/oauth2/token", data={
                "client_id": connector.config.get("client_id", ""), "client_secret": secret,
            })
            auth.raise_for_status()
            token = auth.json()["access_token"]
            response = client.get(base + "/devices/queries/devices/v1", params={"limit": 1},
                                  headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            count = len(response.json()["resources"])
            return "checked", f"Host inventory access verified. Read {count} host in the first page."
    except httpx.HTTPStatusError as exc:
        return "error", f"Provider returned HTTP {exc.response.status_code}. Check credentials and read permissions."
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return "error", "Could not complete the provider read check."
