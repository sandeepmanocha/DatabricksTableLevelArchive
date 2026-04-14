from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_spark():
    return MagicMock()


@pytest.fixture
def mock_run_context():
    from src.utils import RunContext

    return RunContext(
        settings={
            "audit_catalog": "test_catalog",
            "audit_schema": "audit",
            "default_retention_years": 7,
            "archive_base_path_prefix": "abfss://archive@storage.dfs.core.windows.net",
            "secret_scope": "archive-dev",
            "timezone": "UTC",
            "schema_templates_table": "test_catalog.config.schema_templates",
            "table_configs_table": "test_catalog.config.table_configs",
        },
        secrets={"warehouse_id": "test-warehouse-id"},
        job_context={
            "workspace_id": "123",
            "job_id": None,
            "job_run_id": None,
            "task_run_id": None,
        },
        archive_run_id="test-run-id-0000",
    )


@pytest.fixture
def mock_audit():
    return MagicMock()
