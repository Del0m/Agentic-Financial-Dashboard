from types import SimpleNamespace

import pytest

from app import config
from scripts import setup_models


class FakeOllama:
    def __init__(self, have=(), down=False, broken=()):
        self.have, self.down, self.broken = list(have), down, broken
        self.pulled = []

    def list(self):
        if self.down:
            raise ConnectionError("refused")
        return SimpleNamespace(models=[SimpleNamespace(model=m) for m in self.have])

    def pull(self, name, stream=False):
        if name in self.broken:
            raise RuntimeError("pull model manifest: file does not exist")
        self.pulled.append(name)
        return iter([SimpleNamespace(status="pulling", completed=50, total=100),
                     SimpleNamespace(status="success", completed=None, total=None)])


@pytest.fixture(autouse=True)
def models_and_kb(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_MODEL", "chat:14b")
    monkeypatch.setattr(config, "OLLAMA_EMBED_MODEL", "embed")
    monkeypatch.setattr(setup_models, "WAIT_SECONDS", 0)
    calls = {"fetch": 0, "ingest": 0, "count": 0}
    monkeypatch.setattr(setup_models, "fetch_sources", lambda: calls.__setitem__("fetch", calls["fetch"] + 1))
    monkeypatch.setattr(setup_models, "kb_count", lambda: calls["count"])
    monkeypatch.setattr(setup_models, "ingest_kb", lambda: calls.__setitem__("ingest", calls["ingest"] + 1))
    return calls


def test_pulls_both_models_then_builds_the_kb(models_and_kb):
    fake = FakeOllama()
    assert setup_models.main(fake) == 0
    assert fake.pulled == ["chat:14b", "embed"]
    assert models_and_kb["ingest"] == 1


# an untagged name means :latest, and a model already there mustn't need the network
def test_models_already_there_are_skipped(models_and_kb):
    fake = FakeOllama(have=["chat:14b", "embed:latest"])
    assert setup_models.main(fake) == 0
    assert fake.pulled == []


def test_a_built_kb_is_not_rebuilt(models_and_kb):
    models_and_kb["count"] = 89
    assert setup_models.main(FakeOllama()) == 0
    assert models_and_kb["fetch"] == 0 and models_and_kb["ingest"] == 0


def test_ollama_down_says_how_to_fix_it(models_and_kb, capsys):
    assert setup_models.main(FakeOllama(down=True)) == 1
    out = capsys.readouterr().out
    assert "Ollama" in out and "docker compose run --rm models" in out
    assert models_and_kb["ingest"] == 0


def test_a_failed_pull_stops_before_the_kb(models_and_kb, capsys):
    assert setup_models.main(FakeOllama(broken=["embed"])) == 1
    assert "embed" in capsys.readouterr().out
    assert models_and_kb["ingest"] == 0


# the copyrighted page isn't in the repo; the other sources are enough for the demo
def test_a_failed_page_fetch_still_builds_the_kb(models_and_kb, monkeypatch, capsys):
    def boom():
        raise OSError("no network")
    monkeypatch.setattr(setup_models, "fetch_sources", boom)
    assert setup_models.main(FakeOllama()) == 0
    assert models_and_kb["ingest"] == 1
    assert "without" in capsys.readouterr().out
