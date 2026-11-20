"""VS.5c in discovery: a function that runs a container image gets the image and digest Lambda resolved; a zip function
is never asked for (GetFunction would hand back a link to its code)."""

from __future__ import annotations

import base64

import pytest

from app.connectors.aws import Aws
from app.connectors.aws_enrich import Enricher
from app.connectors.base import SourceError

REGION = "ap-southeast-2"
IMAGE_FN = f"arn:aws:lambda:{REGION}:111122223333:function:payments-worker"
ZIP_FN = f"arn:aws:lambda:{REGION}:111122223333:function:shop-api"
RESOLVED = f"111122223333.dkr.ecr.{REGION}.amazonaws.com/payments-worker@sha256:{'a' * 64}"


def test_an_image_function_carries_its_resolved_image_and_a_zip_function_is_not_asked():
    calls = []

    def call(service, method, *, region="", **params):
        calls.append((service, method, params.get("FunctionName")))
        if method == "list_functions":
            return {"Functions": [
                {"FunctionArn": IMAGE_FN, "PackageType": "Image", "Role": "r"},
                {"FunctionArn": ZIP_FN, "PackageType": "Zip", "Runtime": "python3.12", "Role": "r"}]}
        assert (method, params["FunctionName"]) == ("get_function", IMAGE_FN)
        return {"Code": {"ImageUri": RESOLVED.replace(f"@sha256:{'a' * 64}", ":latest"), "ResolvedImageUri": RESOLVED}}

    enricher = Enricher(call, 50)
    assert enricher.describe(IMAGE_FN)["image"] == RESOLVED
    assert "image" not in enricher.describe(ZIP_FN)
    assert [call for call in calls if call[1] == "get_function"] == [("lambda", "get_function", IMAGE_FN)]


class _Ecr:
    def __init__(self):
        self.asked = []

    def __call__(self, service, region, _credentials):
        assert service in ("sts", "ecr"), service
        return self

    def assume_role(self, **_params):
        return {"Credentials": {}, "AssumedRoleUser": {"Arn": "arn:aws:sts::111122223333:assumed-role/r/s"}}

    def get_authorization_token(self, **params):
        self.asked.append(params)
        return {"authorizationData": [{"authorizationToken": base64.b64encode(b"AWS:pull-token").decode()}]}


def test_the_registry_sign_in_uses_the_images_own_account_and_region():
    clients = _Ecr()
    source = Aws({"role_arn": "arn:aws:iam::111122223333:role/r", "external_id": "x" * 12}, clients=clients,
                 sleep=lambda _s: None)
    registry, user, password = source.registry_login(RESOLVED)
    assert (registry, user, password) == (f"111122223333.dkr.ecr.{REGION}.amazonaws.com", "AWS", "pull-token")
    assert clients.asked == [{"registryIds": ["111122223333"]}]
    with pytest.raises(SourceError, match="is not an ECR registry"):
        source.registry_login("public.ecr.aws/lambda/python:3.12")
