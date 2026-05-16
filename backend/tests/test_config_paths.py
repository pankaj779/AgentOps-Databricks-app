"""Ensure settings resolve backend/.env regardless of process cwd."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

# Repository layout: backend/tests/ -> backend/
_BACKEND_ROOT = Path(__file__).resolve().parent.parent


class TestConfigPaths(unittest.TestCase):
    def test_backend_root_contains_app_package(self) -> None:
        self.assertTrue((_BACKEND_ROOT / "app" / "config.py").is_file())

    def test_settings_env_file_is_backend_dotenv(self) -> None:
        from app.config import Settings, _BACKEND_DIR

        self.assertEqual(_BACKEND_DIR.resolve(), _BACKEND_ROOT.resolve())
        env_file = Settings.model_config.get("env_file")
        self.assertIsInstance(env_file, str)
        self.assertEqual(Path(env_file).resolve(), (_BACKEND_ROOT / ".env").resolve())

    def test_overview_reads_inference_table_with_foreign_cwd(self) -> None:
        """Regression: load_dotenv(cwd-only) broke when uvicorn ran from repo root."""
        if not (_BACKEND_ROOT / ".env").is_file():
            self.skipTest("backend/.env not present")

        from app.config import get_settings

        get_settings.cache_clear()
        prior = os.getcwd()
        try:
            # Any directory that does not contain .env
            os.chdir(Path.home())
            get_settings.cache_clear()
            s = get_settings()
            self.assertTrue(
                s.inference_table_fqn.strip(),
                "AGENTOPS_INFERENCE_TABLE should load from backend/.env",
            )
        finally:
            os.chdir(prior)
            get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
