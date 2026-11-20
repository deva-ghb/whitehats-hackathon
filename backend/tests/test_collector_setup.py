"""COL.4: a collector installs from the published image or the served program, its name stays one argument, and its
token never sits on a command line."""

import hashlib
import shlex

from app.services import deploy


def test_the_setup_files_name_the_published_image_quote_the_name_and_keep_the_token_off_the_command_line(client, ceo):
    out = client.post("/api/collectors", headers=ceo, json={"name": "payments prod net", "kind": "network",
                                                             "mode": "docker"}).json()
    token, files = out["token"], out["manifests"]
    assert "public.ecr.aws/f8g3u1i7/whitehats-collector:1.0.0" in files["docker"]
    run = shlex.split(files["docker"].replace("\\\n", " "))
    assert run[run.index("--name", run.index(deploy.AGENT_IMAGE)) + 1] == "payments prod net"  # one argument
    assert f"WH_TOKEN={token}" in run and "--token" not in run
    unit = files["systemd"]
    start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert token not in start and "'payments prod net'" in start
    assert f'Environment="WH_TOKEN={token}"' in unit
    assert "/api/agent/collector.py" in unit and deploy.agent_sha256() in unit
    assert "chmod 0644 /opt/whitehats/collector.py" in unit  # readable by the service's own user
    assert files["kubernetes"].count(token) == 1 and "$(WH_TOKEN)" not in files["kubernetes"]  # in the secret alone


def test_the_program_is_served_with_its_fingerprint(client):
    got = client.get("/api/agent/collector.py")
    assert got.status_code == 200 and got.content == deploy.AGENT_SCRIPT.read_bytes()
    assert got.headers["x-sha256"] == hashlib.sha256(got.content).hexdigest() == deploy.agent_sha256()
