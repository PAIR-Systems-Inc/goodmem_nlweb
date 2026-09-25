"""The documented NLWeb configuration is the one NLWeb actually uses.

nlweb-core (0.7) passes every key of a provider entry except ``import_path``
and ``class_name`` to the constructor as a keyword argument, and its ``ask``
handler asks for the providers named ``default``
(``config.get_retrieval_provider("default")``,
``config.get_object_lookup_provider("default")``).

Before this fix the README and docstrings nested the options under an
``options:`` mapping, which reached the constructor as one ``options={...}``
keyword and failed with "needs space_id or space_name"; and they named the
entry ``goodmem``, which the handler never asks for. These tests read the YAML
straight out of the README and the docstrings, load it with nlweb-core's own
``load_config`` and ``initialize_providers``, and fetch the providers the way
the handler does.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
import re

from nlweb_core import config as nlweb_config
import pytest

import nlweb_goodmem
from nlweb_goodmem import GoodMemObjectLookupProvider, GoodMemRetrievalProvider

README = Path(__file__).resolve().parent.parent / "README.md"
BASE_URL = "https://goodmem.test"
KEY = "gm_test_key_not_a_real_credential"


def _readme_yaml(section: str) -> str:
    """The README's YAML block whose first line is ``section:``."""
    blocks = re.findall(r"```yaml\n(.*?)```", README.read_text(), re.DOTALL)
    matching = [b for b in blocks if b.startswith(f"{section}:\n")]
    assert len(matching) == 1, f"expected one `{section}:` yaml block in the README"
    return matching[0]


def _docstring_yaml(doc: str | None) -> str:
    """The indented ``retrieval:`` example of a docstring, dedented."""
    assert doc
    lines = doc.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "retrieval:")
    indent = len(lines[start]) - len(lines[start].lstrip())
    block = []
    for line in lines[start:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        block.append(line[indent:])
    return "\n".join(block).strip() + "\n"


@pytest.fixture
def nlweb(tmp_path: Path) -> Iterator[Callable[[str], nlweb_config.AppConfig]]:
    """Load YAML as NLWeb does at startup; returns a loader function."""

    def load(yaml_text: str) -> nlweb_config.AppConfig:
        yaml_text = yaml_text.replace("https://localhost:8080", BASE_URL)
        path = tmp_path / "config.yaml"
        path.write_text(yaml_text.replace("gm_…", KEY))
        loaded = nlweb_config.load_config(path)
        nlweb_config.initialize_providers(loaded)
        return loaded

    yield load
    # Reset nlweb-core's module-level provider maps. Nothing needs closing:
    # the SDK client is created on the first request, and none is made here.
    for name in (
        "_embedding_provider_map",
        "_generative_provider_map",
        "_scoring_provider_map",
        "_site_config_provider_map",
        "_object_storage_provider_map",
        "_retrieval_provider_map",
    ):
        _reset_provider_map(name)


def _reset_provider_map(name: str) -> None:
    assert hasattr(nlweb_config, name), name
    setattr(nlweb_config, name, None)


RETRIEVAL_SOURCES = {
    "README": lambda: _readme_yaml("retrieval"),
    "nlweb_goodmem.__doc__": lambda: _docstring_yaml(nlweb_goodmem.__doc__),
    "GoodMemRetrievalProvider.__doc__": lambda: _docstring_yaml(
        GoodMemRetrievalProvider.__doc__
    ),
}


def _assert_built(provider: object, cls: type) -> None:
    assert isinstance(provider, cls)
    inner = getattr(provider, "_provider", provider)
    assert inner.space_name == "nlweb"
    assert inner._conn._base_url == BASE_URL
    assert inner._conn._api_key == KEY


@pytest.mark.parametrize("source", list(RETRIEVAL_SOURCES))
def test_the_documented_retrieval_config_is_what_the_ask_handler_gets(
    nlweb, source: str
) -> None:
    loaded = nlweb(RETRIEVAL_SOURCES[source]())
    # nlweb_core.handler: config.get_retrieval_provider("default")
    _assert_built(loaded.get_retrieval_provider("default"), GoodMemRetrievalProvider)


def test_the_three_documented_retrieval_configs_are_the_same() -> None:
    assert len({fn() for fn in RETRIEVAL_SOURCES.values()}) == 1


def test_the_documented_object_storage_config_is_what_the_handler_gets(
    nlweb,
) -> None:
    loaded = nlweb(_readme_yaml("object_storage"))
    # nlweb_core.handler: config.get_object_lookup_provider("default")
    _assert_built(
        loaded.get_object_lookup_provider("default"), GoodMemObjectLookupProvider
    )


def test_an_env_suffixed_key_is_read_from_the_environment(
    nlweb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The README's note on ``api_key_env``: nlweb-core resolves it."""
    monkeypatch.setenv("GOODMEM_API_KEY", KEY)
    yaml_text = _readme_yaml("retrieval").replace(
        "api_key: gm_…", "api_key_env: GOODMEM_API_KEY"
    )
    assert "api_key_env" in yaml_text
    provider = nlweb(yaml_text).get_retrieval_provider("default")
    assert provider._conn._api_key == KEY
