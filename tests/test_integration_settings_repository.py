from app.integrations.repository import IntegrationSettingsRepository


def test_baidu_setting_can_preserve_and_clear_secret(session):
    repository = IntegrationSettingsRepository(session)
    item = repository.save("baidu_maps", True,
        {"default_region": "广州", "test_keyword": "三溪地铁站"}, "secret-ak")
    session.commit()

    preserved = repository.save("baidu_maps", False,
        {"default_region": "深圳", "test_keyword": "福田站"}, None)
    assert preserved.secret_value == "secret-ak"
    assert preserved.config_json["default_region"] == "深圳"

    repository.clear_secret("baidu_maps")
    assert repository.get("baidu_maps").secret_value is None
    assert repository.get("baidu_maps").enabled is False


def test_new_disabled_setting_can_be_saved_without_secret(session):
    item = IntegrationSettingsRepository(session).save(
        "baidu_maps", False, {"default_region": "广州", "test_keyword": "三溪地铁站"}, None)

    assert item.provider == "baidu_maps"
    assert item.secret_value is None
    assert item.enabled is False
