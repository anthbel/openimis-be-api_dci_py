"""
Django app configuration for DCI API module.
"""
from django.apps import AppConfig

MODULE_NAME = 'api_dci'

DEFAULT_CFG = {
    # {reg_record_type: dotted path of a record provider}, see api_dci.record_providers.
    "record_providers": {},
}


class ApiDciConfig(AppConfig):
    """
    Configuration class for the DCI API module.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = MODULE_NAME
    verbose_name = 'DCI API'

    record_providers = {}

    def ready(self):
        """
        Perform initialization when Django starts.
        """
        from core.models import ModuleConfiguration
        from .record_providers import load_record_providers

        cfg = ModuleConfiguration.get_or_default(MODULE_NAME, DEFAULT_CFG)
        self._load_config(cfg)
        # An unresolvable provider path stops startup.
        load_record_providers(ApiDciConfig.record_providers)

        # Import and bind subscription signals
        from .signals import bind_service_signals
        bind_service_signals()

    @classmethod
    def _load_config(cls, cfg):
        for field in cfg:
            if hasattr(cls, field):
                setattr(cls, field, cfg[field])
