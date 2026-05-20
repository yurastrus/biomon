def test_default_config_is_production():
    from config import config, ProductionConfig
    assert config['default'] is ProductionConfig
