"""Unity Catalog FQN validation (AI Gateway payload tables often contain hyphens)."""

from __future__ import annotations

import unittest

from app.databricks.fqn import quote_fqn, quote_schema_fqn, validate_fqn, validate_schema_fqn


class TestFqn(unittest.TestCase):
    def test_hyphenated_table_name(self) -> None:
        fqn = "agentops.agent_logs.gemma-3-model_payload"
        self.assertTrue(validate_fqn(fqn))
        self.assertEqual(
            quote_fqn(fqn),
            "`agentops`.`agent_logs`.`gemma-3-model_payload`",
        )

    def test_schema_fqn(self) -> None:
        self.assertTrue(validate_schema_fqn("agentops.agent_logs"))
        self.assertEqual(quote_schema_fqn("agentops.agent_logs"), "`agentops`.`agent_logs`")


if __name__ == "__main__":
    unittest.main()
