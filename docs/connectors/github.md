# GitHub

WhiteHats reads GitHub through the WhiteHats GitHub App.
Personal access tokens are not accepted for reading: an App lets the customer choose the repositories, its permissions are fixed, and every token it uses expires within an hour.

## What WhiteHats reads

- Discovery: each repository the App is installed on, its branches, whether the default branch is protected, whether it has a CODEOWNERS file, its dependency manifests, and its GitHub Actions workflows.
- New commits on each default branch, and the new head of every other branch, polled every minute.
- Pushes, pull requests and finished workflow runs, as they happen, by webhook.

A commit is reported once, whether the webhook or the poll sees it first.
A webhook delivery whose signature does not match is refused.

- A repository scan, when someone starts an import from a repository: the file tree of the default branch, then each file the scan asks for (Terraform, manifests, lock files, Dockerfiles, workflows, and the scripts a workflow runs), read as blobs. Nothing is written, and file text is not kept: only what it says about the estate (see Importing an estate).

## What WhiteHats does with a push

A push or a pull request on a repository one of your projects holds is a change.
WhiteHats keeps one change for each commit that is new, however it learned of it: the webhook and the poll can both report the same commit, and it is still read and judged once.
A burst of commits on one branch is judged as one change, from the oldest of them to the newest.

For each change WhiteHats reads only what changed.
It asks GitHub for the difference between the new commit and the last commit it judged on that branch (or, for a new branch, the default branch), then reads each file in that difference, and no other file, as it was at the new commit.
Files that cannot be read safely or are too large are skipped, and the change says which and why.
The files live in a temporary folder while the Decisions API judges them and are deleted afterwards.
WhiteHats keeps the answer (the verdict, each suspect with its file and lines, which engines ran) and the times, never the code.

If an engine could not run, the change is marked incomplete rather than clean.
If GitHub cannot be read, the change waits and is tried again on the next pass, three times in all, and then shows why it failed.

Judging runs after the webhook has been answered, so GitHub never waits for it.
The polled changes are picked up within ten seconds of the poll that found them.

## The App's permissions

| Permission | Level | Used for |
| --- | --- | --- |
| Metadata | Read | List the repositories and their branches |
| Contents | Read | Read new commits; find CODEOWNERS and dependency manifests |
| Actions | Read | List workflows and their runs |
| Pull requests | Read and write | Read open pull requests; write is only for WhiteHats' comments on them |
| Checks | Read and write | The "WhiteHats" check on a pull request |

Webhook events: Push, Pull request, Workflow run.

On a pull request, WhiteHats adds one inline comment per finding on its line (or on the file, when the line is not part of the pull request's change), never twice for the same finding, and answers that comment once when a later commit removes the weakness. Those comments and the "WhiteHats" check are the only things it writes.

WhiteHats refuses to run when an installation holds more than this list, for example Contents write or any Administration access, and says which permission to lower.

## Connect

1. Start the install from WhiteHats.
   The GitHub connection node on the canvas has the *Install the App* button (STUDIO.4); the link comes from `POST /api/github/install`, and GitHub sends the person back to the Studio with that node selected.
   The link is valid for 10 minutes and only for the person who asked for it.
2. On GitHub, choose the account and the repositories, and install.
3. GitHub sends you back to WhiteHats.
   WhiteHats keeps the installation only if GitHub lists it among the installations you can manage, then checks it: one line per permission, ok or missing.

Only an Account owner or Account admin can connect GitHub.

## For the people running a WhiteHats server

One GitHub App serves every account on the server.
Create it once in GitHub under *Settings, Developer settings, GitHub Apps, New GitHub App*:

- Homepage URL: the server's public address.
- Callback URL: `<public address>/api/github/callback`, with *Request user authorization (OAuth) during installation* switched on.
- Webhook: active, URL `<public address>/api/github/webhook`, secret the value of `GITHUB_WEBHOOK_SECRET`.
- Permissions and events: the table above.

Then set in the server's environment: `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY_PATH` (the path to the downloaded private key file, never the key itself) and `GITHUB_WEBHOOK_SECRET`.

## Checking the timings

A push must be seen by webhook within 10 seconds and by polling within 70 seconds.
After a push, compare the repository's push time on GitHub with the time WhiteHats recorded: the connection's last event for a webhook, its last read for a poll.
GitHub's *Recent deliveries* page for the App shows each delivery and WhiteHats' answer.

From `platform/backend`, with the server's own settings, `uv run python scripts/change_timings.py` lists each change with its source (webhook or poll), its status and verdict, and the seconds from the push to the change being kept, to its files being read, and to the Decisions API's answer.
The Decisions API runs its engines in containers, so a server that judges changes needs Docker.
`tests/send_push.py` replays a push of a commit that already exists to a running server, signed with the webhook secret, to time that path without pushing anything.

Fix Factory still reaches repositories through *Setup, Code repositories*; each must answer `git ls-remote`.
