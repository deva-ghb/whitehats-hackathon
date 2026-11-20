"""An ownership guard was taken out of a change.

The class is "a guard that checked who owns a record was removed". The examples
are small and invented. A comparison that is still present on an added line is
a move, not a removal.
"""

from __future__ import annotations

import re

from ..diff import FileChange
from .base import ChangeRule, Example, Hit

_OWNER = re.compile(
    r"\b(?:owner_id|owner|customer_id|user_id|account_id|tenant_id|created_by|principal_id)\b"
)
_GUARD = re.compile(r"\b(?:if|elif|assert)\b")
_COMPARE = re.compile(r"!=|==")


def _code(line: str) -> str:
    return line.split("#", 1)[0]


def _is_owner_guard(line: str) -> bool:
    code = _code(line)
    return bool(_GUARD.search(code) and _COMPARE.search(code) and _OWNER.search(code))


class OwnerCheckRemoved(ChangeRule):
    def check_change(self, change: FileChange) -> list[Hit]:
        hits: list[Hit] = []
        for number, line in change.removed:
            if not _is_owner_guard(line):
                continue
            fields = set(_OWNER.findall(_code(line)))
            moved = any(
                _is_owner_guard(added) and fields <= set(_OWNER.findall(_code(added)))
                for _, added in change.added
            )
            if moved:
                continue
            hits.append(Hit(self.id, change.path, number, number, "ownership guard removed"))
        return hits


_MATCH = """\
diff --git a/billing/records.py b/billing/records.py
--- a/billing/records.py
+++ b/billing/records.py
@@ -8,3 +8,3 @@ def read_record(record_id, actor_id):
     record = store.get(record_id)
-    if record is None or record.owner_id != actor_id:
+    if record is None:
         raise Missing
"""

_MATCH_ACCOUNT = """\
diff --git a/billing/accounts.py b/billing/accounts.py
--- a/billing/accounts.py
+++ b/billing/accounts.py
@@ -4,2 +4,1 @@ def load(account, actor):
-    if account.customer_id != actor:
-        raise Forbidden
+    return account
"""

_KEPT = """\
diff --git a/billing/records.py b/billing/records.py
--- a/billing/records.py
+++ b/billing/records.py
@@ -4,1 +4,2 @@
-    if record.owner_id != actor_id:
+    if record.owner_id != actor_id:
+        deny()
"""

_COMMENT = """\
diff --git a/billing/records.py b/billing/records.py
--- a/billing/records.py
+++ b/billing/records.py
@@ -1,1 +1,1 @@
-# owner_id == is described here, not enforced on this line
+# owner_id == is still enforced in the function below
"""

_UNRELATED = """\
diff --git a/billing/records.py b/billing/records.py
--- a/billing/records.py
+++ b/billing/records.py
@@ -2,1 +2,1 @@
-    pending = "pending"
+    pending = "pending"  # a new record starts here
"""

RULE = OwnerCheckRemoved(
    id="whitehats.diff.owner-check-removed",
    title="Ownership guard removed",
    weakness="a guard that checked who owns a record was removed",
    cwe=("CWE-639",),
    severity="high",
    must_match=(Example(_MATCH), Example(_MATCH_ACCOUNT)),
    must_not_match=(Example(_KEPT), Example(_COMMENT), Example(_UNRELATED)),
)
