"""Send a push delivery to a running WhiteHats server, signed with GITHUB_WEBHOOK_SECRET, the way GitHub does.

    uv run python tests/send_push.py --url http://127.0.0.1:8145 --branch main --after <commit> [--before <commit>]

This is not GitHub: it replays a push of a commit that already exists, to time what the server does with a
delivery (answer, read the diff, judge it). Nothing is sent to GitHub. The secret is read from the environment
and never printed. `--forge` signs with the wrong key, to see the refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import uuid

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--before", default="0" * 40)
    parser.add_argument("--forge", action="store_true")
    args = parser.parse_args()
    repo = os.environ["PROVING_GROUND_REPO"]
    body = json.dumps({
        "ref": f"refs/heads/{args.branch}", "before": args.before, "after": args.after,
        "installation": {"id": int(os.environ["GITHUB_APP_INSTALLATION_ID"])},
        "repository": {"full_name": repo, "default_branch": "main", "html_url": f"https://github.com/{repo}",
                       "pushed_at": int(time.time())},
        "commits": [{"id": args.after, "message": "A push replayed by send_push.py", "author": {"name": "replay"}}],
    }).encode()
    key = "forged" if args.forge else os.environ["GITHUB_WEBHOOK_SECRET"]
    headers = {"X-GitHub-Event": "push", "X-GitHub-Delivery": str(uuid.uuid4()),
               "X-Hub-Signature-256": "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest(),
               "Content-Type": "application/json"}
    sent = time.time()
    answer = httpx.post(f"{args.url}/api/github/webhook", content=body, headers=headers, timeout=30)
    print(f"sent at {time.strftime('%H:%M:%S', time.localtime(sent))}, answered {answer.status_code} "
          f"in {time.time() - sent:.2f}s: {answer.text}")
    return 0 if answer.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
