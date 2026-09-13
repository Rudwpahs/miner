import importlib.util


def test_source_identity_validator_module_exists():
    spec = importlib.util.find_spec("basketball_miner.source_identity")
    assert spec is not None
