"""Песочница имитации: боевое недостижимо."""
from __future__ import annotations

import copy

import pytest
import yaml

from listam.config import Config
from tests.sim.sandbox import PROJECT, SimRefused, build_config, patched_fetcher, refuse


def test_the_sim_config_keeps_prod_cycles_and_points_everything_into_out(tmp_path):
    config = build_config(tmp_path)
    prod = yaml.safe_load((PROJECT / "config" / "prod.yaml").read_text(encoding="utf-8"))
    assert config.get("schedule.cycles") == prod["schedule"]["cycles"]
    assert config.get("match") == prod["match"]
    assert config.get("notify.kind") == "stdout"
    assert config.get("rate.kind") == "fixed"
    assert config.get("funnel.delay_seconds") == 0
    for key in ("storage.directory", "storage.work_dir", "schedule.log_dir",
                "requests.path", "scrape.pages_dir", "export.path"):
        assert str(config.get(key)).startswith(str(tmp_path)), key
    assert "${" not in (tmp_path / "sim.yaml").read_text(encoding="utf-8")
    assert "R-3,ПРИМЕР широкая" in (tmp_path / "requests.csv").read_text(encoding="utf-8")
    refuse(config)                                   # своё не отвергает


def _with(config: Config, key: str, value) -> Config:
    data = copy.deepcopy(config.data)
    node = data
    *path, last = key.split(".")
    for part in path:
        node = node[part]
    node[last] = value
    return Config(data, env="sim", path=config.path)


@pytest.mark.parametrize("key,value", [
    ("notify.kind", "telegram"),
    ("rate.kind", "rate_am"),
    ("scrape.kind", "playwright"),
])
def test_refuses_a_live_adapter(tmp_path, key, value):
    with pytest.raises(SimRefused):
        refuse(_with(build_config(tmp_path), key, value))


def test_refuses_a_path_inside_project_data(tmp_path):
    config = _with(build_config(tmp_path), "storage.work_dir", str(PROJECT / "data"))
    with pytest.raises(SimRefused):
        refuse(config)


def test_every_fetcher_factory_is_the_sim_one(tmp_path):
    import listam.crawler
    import listam.find
    import listam.pages
    import listam.wiring

    config = build_config(tmp_path)
    sentinel = object()
    with patched_fetcher(sentinel):
        for module in (listam.crawler, listam.pages, listam.find, listam.wiring):
            assert module.build_fetcher(config) is sentinel
            assert module.build_fetcher(config, delay_seconds=5) is sentinel
    assert listam.crawler.build_fetcher is listam.wiring.build_fetcher
