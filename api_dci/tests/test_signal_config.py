"""
Tests for the DCI_SUBSCRIBE_INDIVIDUAL_SIGNAL setting.

bind_service_signal is patched in api_dci.signals, so no handler is bound
to the real service signals.
"""
import os
from unittest import mock

from django.test import SimpleTestCase

from api_dci import signals
from api_dci.configurations import DCIConfig

SETTING = "DCI_SUBSCRIBE_INDIVIDUAL_SIGNAL"


class SubscribeIndividualSignalSettingTestCase(SimpleTestCase):

    def _bind_with(self, value):
        with mock.patch.dict(os.environ):
            os.environ.pop(SETTING, None)
            if value is not None:
                os.environ[SETTING] = value
            enabled = DCIConfig.get_subscribe_individual_signal()
            with mock.patch.object(signals, "bind_service_signal") as bind:
                signals.bind_service_signals()
        return enabled, bind

    def test_unset_setting_binds_no_individual_signal(self):
        enabled, bind = self._bind_with(None)
        self.assertFalse(enabled)
        bind.assert_not_called()

    def test_false_setting_binds_no_individual_signal(self):
        enabled, bind = self._bind_with("False")
        self.assertFalse(enabled)
        bind.assert_not_called()

    def test_true_setting_binds_update_and_delete_signals(self):
        for value in ("True", "true"):
            with self.subTest(value=value):
                enabled, bind = self._bind_with(value)
                self.assertTrue(enabled)
                bound = [c.args[0] for c in bind.call_args_list]
                self.assertEqual(
                    bound,
                    ["individual_service.create_or_update", "individual_service.delete"],
                )
