import os
import unittest
from unittest.mock import patch

from src.config import load_settings


class SettingsTest(unittest.TestCase):
    def test_blank_optional_env_values_use_defaults(self):
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "",
                "OPENAI_MODEL": "",
                "LLM_REQUEST_DELAY_SECONDS": "",
                "MAX_ITEMS_PER_RUN": "",
                "MAX_ITEMS_PER_SOURCE": "   ",
                "LOOKBACK_DAYS": "",
            },
            clear=False,
        ):
            settings = load_settings()

        self.assertEqual(settings.llm_provider, "openai")
        self.assertEqual(settings.openai_model, "gpt-5.4-mini")
        self.assertEqual(settings.llm_request_delay_seconds, 25)
        self.assertEqual(settings.max_items_per_run, 40)
        self.assertEqual(settings.max_items_per_source, 8)
        self.assertEqual(settings.lookback_days, 7)


if __name__ == "__main__":
    unittest.main()
