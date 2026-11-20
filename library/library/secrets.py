"""Find secret-shaped strings in a clone document.

Token shapes come from detect-secrets plugins, so new formats arrive with that
package. A short extra list covers formats those plugins still miss. The
matched text itself is never returned.
"""

from __future__ import annotations

import re
from functools import cache

from detect_secrets.plugins.aws import AWSKeyDetector
from detect_secrets.plugins.azure_storage_key import AzureStorageKeyDetector
from detect_secrets.plugins.basic_auth import BasicAuthDetector
from detect_secrets.plugins.github_token import GitHubTokenDetector
from detect_secrets.plugins.jwt import JwtTokenDetector
from detect_secrets.plugins.keyword import KeywordDetector
from detect_secrets.plugins.private_key import PrivateKeyDetector
from detect_secrets.plugins.slack import SlackDetector
from detect_secrets.plugins.stripe import StripeDetector

# High-entropy and public-IP plugins stay off: they flag ordinary long strings
# and a company's own address ranges. These patterns cover the gaps the plugins
# above do not (fine-grained GitHub PATs, Stripe test keys, short live keys,
# Service Bus keys, and SAS signatures).
_EXTRA: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GitHub fine-grained PAT", re.compile(r"github_pat_[0-9A-Za-z_]{20,}")),
    ("Stripe key", re.compile(r"(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{8,}")),
    ("Azure account key", re.compile(r"AccountKey=[0-9A-Za-z+/=]{16,}")),
    ("Azure shared access key", re.compile(r"SharedAccessKey=[0-9A-Za-z+/=]{16,}")),
    ("Azure SAS signature", re.compile(r"(?:^|[?&;])sig=[0-9A-Za-z%]{16,}")),
)


@cache
def _plugins() -> tuple[object, ...]:
    return (
        AWSKeyDetector(),
        AzureStorageKeyDetector(),
        BasicAuthDetector(),
        GitHubTokenDetector(),
        JwtTokenDetector(),
        KeywordDetector(),
        PrivateKeyDetector(),
        SlackDetector(),
        StripeDetector(),
    )


def secret_kinds(text: str) -> list[str]:
    """Names of the secret types found in text. The secret text is not included."""
    found: list[str] = []
    for plugin in _plugins():
        if any(plugin.analyze_string(text)):
            found.append(plugin.secret_type)
    for name, pattern in _EXTRA:
        if pattern.search(text):
            found.append(name)
    return found


def find_secret_values(document: object) -> list[str]:
    """Return one message per string that looks like a secret. Vault refs are skipped."""
    errors = []
    for path, text in _strings(document, ""):
        if text.startswith("vault://"):
            continue
        kinds = secret_kinds(text)
        if kinds:
            errors.append(f"{path}: looks like a {kinds[0]}")
    return errors


def _strings(value: object, path: str) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path or "document", value)]
    if isinstance(value, dict):
        found = []
        for key, item in value.items():
            found.extend(_strings(item, f"{path}/{key}" if path else str(key)))
        return found
    if isinstance(value, list):
        found = []
        for index, item in enumerate(value):
            found.extend(_strings(item, f"{path}/{index}"))
        return found
    return []
