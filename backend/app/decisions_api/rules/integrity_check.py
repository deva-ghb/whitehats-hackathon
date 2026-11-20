"""A signature or integrity check was removed or commented out.

The class is "an integrity check was taken away". Commenting the call out counts,
because the check no longer runs. Moving the same call to an added line does not.
"""

from __future__ import annotations

import re

from ..diff import FileChange
from .base import ChangeRule, Example, Hit

_CALL = re.compile(
    r"\b([\w.]*(?:signature|hmac|checksum|integrity)[\w.]*)\s*\(",
    re.IGNORECASE,
)


def _code(line: str) -> str:
    return line.split("#", 1)[0]


class IntegrityCheckRemoved(ChangeRule):
    def check_change(self, change: FileChange) -> list[Hit]:
        added = "\n".join(_code(line) for _, line in change.added)
        hits: list[Hit] = []
        for number, line in change.removed:
            match = _CALL.search(_code(line))
            if match is None:
                continue
            name = match.group(1)
            if re.search(rf"\b{re.escape(name)}\s*\(", added):
                continue
            hits.append(Hit(self.id, change.path, number, number, "integrity check removed"))
        return hits


_MATCH = """\
diff --git a/billing/hooks.py b/billing/hooks.py
--- a/billing/hooks.py
+++ b/billing/hooks.py
@@ -3,4 +3,3 @@ def accept(payload, header, key):
 def accept(payload, header, key):
-    if not verify_signature(payload, header, key):
-        raise Rejected
+    # verify_signature is paused while the sender is fixed
     store(payload)
"""

_MATCH_HMAC = """\
diff --git a/billing/hooks.py b/billing/hooks.py
--- a/billing/hooks.py
+++ b/billing/hooks.py
@@ -2,2 +2,1 @@ def digest(body, secret):
-    return hmac.new(secret, body).hexdigest()
+    return body
"""

_KEPT = """\
diff --git a/billing/hooks.py b/billing/hooks.py
--- a/billing/hooks.py
+++ b/billing/hooks.py
@@ -2,1 +2,1 @@
-    if not verify_signature(payload, header, key):
+    if not verify_signature(payload, header, key):
"""

_COMMENT_ONLY = """\
diff --git a/billing/hooks.py b/billing/hooks.py
--- a/billing/hooks.py
+++ b/billing/hooks.py
@@ -1,1 +1,1 @@
-# verify_signature(body) is required before accept
+# verify_signature(body) is still required before accept
"""

RULE = IntegrityCheckRemoved(
    id="whitehats.diff.integrity-check-removed",
    title="Integrity check removed",
    weakness="an integrity or signature check was removed or commented out",
    cwe=("CWE-345",),
    severity="high",
    must_match=(Example(_MATCH), Example(_MATCH_HMAC)),
    must_not_match=(Example(_KEPT), Example(_COMMENT_ONLY)),
)
