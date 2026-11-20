"""VS.2: the house rules' 23 regular expressions do exactly what was put to Deva (journal "VS.2 reopen", answered on
2026-10-31): each matches its must-match examples and none of its must-not-match ones. The named patterns are the
compiled objects in the rules; the three written inline (storage_key lines 56 and 59, integrity_check line 33) are
repeated here as written there."""

from __future__ import annotations

import re

import pytest

from app.decisions_api.rules import (
    image_pin,
    integrity_check,
    owner_check,
    sensitive_output,
    storage_key,
    swallowed_error,
    unsafe_load,
)
from app.decisions_api.scanners import semgrep

# (number, pattern, must match, must not match); `search` unless the code uses `match`.
CASES = [
    (1, owner_check._OWNER, ['if order.owner_id != user.id:', 'assert row["customer_id"] == me'], ["ownership = 1", "user_identity"]),
    (2, owner_check._GUARD, ["if x:", "    elif y:", "assert z"], ["verify = 1", "classify(x)"]),
    (3, owner_check._COMPARE, ["a != b", "a == b"], ["a = b", "a <= b"]),
    (4, swallowed_error._EXCEPT, ["except:", "    except Exception:  # ignore"], ["except ValueError:", "except Exception as exc:"]),
    (5, swallowed_error._EXCEPT_PASS, ["except: pass", "except Exception: pass  # fine"], ["except Exception: log(e)"]),
    (6, swallowed_error._PASS, ["    pass", "pass  # nothing"], ["password = x", "pass_through()"]),
    (7, sensitive_output._SINK, ["print(x)", 'logging.info("a")', "logger.warning(x)"], ["blueprint(x)", "logging.getLogger()"]),
    (8, sensitive_output._INTERPOLATION, ['f"{token}"', '"{user.name}"'], ['"{ }"', '"{}"']),
    (9, sensitive_output._SECRET_NAME, ["Authorization", "db_password", "API_KEY"], ["author", "token_count"]),
    (10, sensitive_output._RECORD, ["customer", "User"], ["customers", "user_id"]),
    (11, sensitive_output._ENVIRON, ['os.environ.get("API_KEY")', "getenv('TOKEN')"], ['os.environ["API_KEY"]', 'environ_get("x")']),
    (12, image_pin._FROM, ["FROM python:3.12-slim", "from public.ecr.aws/lambda/python:3.12 AS base"], ["# FROM x", "RUN echo FROM"]),
    (13, storage_key._DEF, ["def handler(event):", "    async def get(self, key):"], ["undef(x)", "# def x("]),
    (14, storage_key._ASSIGN, ["key = name"], ["key = name.strip()", "a == b"]),
    (15, storage_key._STORAGE, ["s3.get_object(Bucket=b, Key=k)"], ["get_objects(", "my_get_object_cache"]),
    (16, re.compile(r"(?:Key|key)\s*=\s*(\w+)$"), ["Key=receipt_id", "key = name"], ['Key=f"x/{id}"', "Key=build(id)"]),
    (17, re.compile(r"\w+$"), ["receipt_id"], ['"receipts/" + id', "build(id)"]),
    (18, unsafe_load._YAML, ["yaml.load(text)", "yaml.unsafe_load(x)"], ["yaml.safe_load(x)", "ruamel_yaml.load(x)"]),
    (19, unsafe_load._SAFE, ["Loader=yaml.SafeLoader"], ["UnsafeLoader", "SafeLoaderX"]),
    (20, unsafe_load._PICKLE, ["pickle.loads(body)"], ["pickle.dumps(x)", "cpickle.loads(x)"]),
    (21, integrity_check._CALL, ["verify_signature(body)", "hmac.compare_digest(a, b)"], ["signature = x", "sign(body)"]),
    (22, re.compile(rf"\b{re.escape('verify_signature')}\s*\("), ["verify_signature("], ["verify_signature_v2("]),
    (23, semgrep._CWE, ["CWE-798: Use of Hard-coded Credentials"], ["CWE-", "cwe 798"]),
]
# The code applies these with re.match (anchored at the start), not search.
MATCHED = {16, 17}


@pytest.mark.parametrize("number, pattern, matches, misses", CASES, ids=[f"pattern-{case[0]}" for case in CASES])
def test_each_pattern_matches_what_it_must_and_nothing_it_must_not(number, pattern, matches, misses):
    apply = pattern.match if number in MATCHED else pattern.search
    assert [text for text in matches if not apply(text)] == [], f"{number} misses what it must match"
    assert [text for text in misses if apply(text)] == [], f"{number} matches what it must not"


def test_the_captures_are_the_names_the_rules_use():
    assert sensitive_output._INTERPOLATION.search('f"{token}"').group(1) == "token"
    assert sensitive_output._INTERPOLATION.search('"{user.name}"').group(1) == "user"
    assert sensitive_output._ENVIRON.search("getenv('TOKEN')").group(1) == "TOKEN"
    assert image_pin._FROM.search("from public.ecr.aws/lambda/python:3.12 AS base").group(1) == "public.ecr.aws/lambda/python:3.12"
    assert semgrep._CWE.search("CWE-798: Use of Hard-coded Credentials").group(0) == "CWE-798"


def test_there_are_exactly_the_23_that_were_put_to_deva():
    assert [case[0] for case in CASES] == list(range(1, 24))
