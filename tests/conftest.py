import os

import pytest

from attribution_engine.config import DatasetConfig
from attribution_engine.pipeline import export, run_pipeline


@pytest.fixture(scope="session")
def cfg():
    return DatasetConfig.starter()


@pytest.fixture(scope="session")
def result(cfg):
    return run_pipeline(cfg)


@pytest.fixture(scope="session")
def tables(result):
    return result.dataset.tables


@pytest.fixture(scope="session")
def exported(result, cfg, tmp_path_factory):
    """Export the starter pipeline to an isolated directory and point the API at it."""
    out = tmp_path_factory.mktemp("data") / "generated"
    export(result, cfg, out)
    os.environ["ATTRIBUTION_DATA_DIR"] = str(out)
    return out
