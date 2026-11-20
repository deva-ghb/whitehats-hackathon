"""Temporary folders are made under the data folder, which the scanners' containers can see (Docker mounts by host
path; a service with a private /tmp made folders Docker could not see, and every engine scanned an empty folder)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.config import DATA_DIR, SCRATCH_DIR


def test_every_temporary_folder_is_under_the_data_folder():
    assert Path(tempfile.gettempdir()) == SCRATCH_DIR == DATA_DIR / "scratch"
    with tempfile.TemporaryDirectory(prefix="whitehats-change-") as folder:
        assert Path(folder).parent == SCRATCH_DIR


def test_when_docker_cannot_see_the_folder_no_engine_runs_and_each_says_why(tmp_path, monkeypatch):
    import importlib

    from app.decisions_api.scanners import SCANNERS

    decide_module = importlib.import_module("app.decisions_api.decide")  # the module, not the function it exports

    ran = []
    monkeypatch.setattr(decide_module, "folders_visible", lambda: False)
    monkeypatch.setattr(decide_module.shutil, "which", lambda name: "/usr/bin/docker")
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    scanners = [lambda root, paths, *, timeout, s=s: ran.append(s.name) for s in SCANNERS]
    for wrapped, real in zip(scanners, SCANNERS, strict=True):
        wrapped.name, wrapped.image = real.name, real.image
    decision = decide_module.decide(tmp_path, None, scanners=scanners)
    assert ran == []
    assert {run.error for run in decision.engines} == {decide_module.NOT_VISIBLE}
    assert len(decision.engines) == len(SCANNERS)  # so the change is incomplete, never called clean


def test_the_probe_says_not_visible_when_the_container_reads_an_empty_folder(monkeypatch):
    import subprocess

    from app.decisions_api import container

    monkeypatch.setattr(container, "_VISIBLE", None)
    monkeypatch.setattr(container.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "No such file"))
    assert container.folders_visible() is False
