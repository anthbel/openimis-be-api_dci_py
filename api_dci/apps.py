"""
Django app configuration for DCI API module.
"""
from django.apps import AppConfig

from core.rights_declaration import RightsDeclaration

MODULE_NAME = 'api_dci'

# Rights by entity then action, in the module block 195700-195799. The person query right opens the Person
# search and detail endpoints and the other endpoints gated by DCIPersonPermissions (subscriptions, notify,
# callbacks, transaction status).
DJANGO_PERMS = {
    "person": {
        "query": ("api_dci.view_dci_person", 195701),
        "create": ("api_dci.add_dci_person", 195702),
        "update": ("api_dci.change_dci_person", 195703),
    },
}

_PERM_CFG = {
    "gql_dci_person_search_perms": ("person", "query"),
    "gql_dci_person_create_perms": ("person", "create"),
    "gql_dci_person_update_perms": ("person", "update"),
}

RIGHTS = RightsDeclaration(MODULE_NAME, DJANGO_PERMS, _PERM_CFG)

configured_perms = RIGHTS.configured

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

    gql_dci_person_search_perms = RIGHTS.perms("person", "query")
    gql_dci_person_create_perms = RIGHTS.perms("person", "create")
    gql_dci_person_update_perms = RIGHTS.perms("person", "update")

    record_providers = {}

    def ready(self):
        """
        Perform initialization when Django starts.
        """
        from core.models import ModuleConfiguration
        from .record_providers import load_record_providers
        from .throttling import check_search_throttle_rate

        cfg = ModuleConfiguration.get_or_default(MODULE_NAME, DEFAULT_CFG)
        self._load_config(cfg)
        # An unresolvable provider path stops startup.
        load_record_providers(ApiDciConfig.record_providers)
        # A malformed search rate stops startup.
        check_search_throttle_rate()

        # Import and bind subscription signals
        from .signals import bind_service_signals
        bind_service_signals()

    @classmethod
    def _load_config(cls, cfg):
        # The rights are not configurable: a stored `_perms` key is ignored.
        for field in cfg:
            if hasattr(cls, field) and not field.endswith("_perms"):
                setattr(cls, field, cfg[field])
