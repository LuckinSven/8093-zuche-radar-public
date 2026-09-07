from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IntegrationSetting


class IntegrationSettingsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, provider: str) -> IntegrationSetting | None:
        return self.session.scalar(select(IntegrationSetting).where(
            IntegrationSetting.provider == provider))

    def save(self, provider: str, enabled: bool, config: dict,
             secret_value: str | None) -> IntegrationSetting:
        item = self.get(provider)
        if item is None:
            item = IntegrationSetting(provider=provider)
            self.session.add(item)
        item.enabled = enabled
        item.config_json = config
        if secret_value is not None:
            item.secret_value = secret_value
        self.session.flush()
        return item

    def clear_secret(self, provider: str) -> None:
        item = self.get(provider)
        if item is not None:
            item.secret_value = None
            item.enabled = False
            self.session.flush()
