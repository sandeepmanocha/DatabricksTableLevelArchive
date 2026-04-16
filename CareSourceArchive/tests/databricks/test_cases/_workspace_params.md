# Workspace Parameters Reference

All test case commands use placeholders. Substitute from the row matching your workspace.

| Placeholder | DEFAULT (e2-demo-field-eng) | fe-sandbox-manocha |
|---|---|---|
| `<PROFILE>` | `DEFAULT` | `fe-sandbox-manocha` |
| `<TARGET>` | `dev` | `dev-serverless` |
| `<CONFIG_TABLE>` | `sandeep_manocha.caresource_audit.global_settings` | `dev2_archive.metadata.global_settings` |
| `<SOURCE_CATALOG>` | `sandeep_manocha` | `dev2_archive` |
| `<SOURCE_SCHEMA>` | `source_data_samples` | `source_data_samples` |
| `<AUDIT_TABLE>` | `sandeep_manocha.caresource_audit.archive_audit_log` | `dev2_archive.metadata.archive_audit_log` |
| `<CONFIG_TABLES_PREFIX>` | `sandeep_manocha.caresource_audit` | `dev2_archive.metadata` |
| `<ARCHIVE_VOL>` | `/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples` | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` |
| `<REHYDRATE_TARGET_SCHEMA>` | `caresource_rehydrated` | `caresource_rehydrated` |
| `<REHYDRATION_AUDIT_TABLE>` | `sandeep_manocha.caresource_audit.rehydration_audit_log` | `dev2_archive.metadata.rehydration_audit_log` |
| `<REHYDRATE_ZERO_TEST_SCHEMA>` | `caresource_rehydrated_22_zero` | `caresource_rehydrated_22_zero` |

> **IMPORTANT:** Always pass `source_catalog` and `source_schema` as explicit params to archive and scanner run commands. Omitting them has caused silent failures in past tests.

## Adding a new workspace

1. Deploy the bundle to the new target (see `docs/runbooks/dab-commands.md`).
2. Run `setup_config_tables` and `seed_config` to create the metadata tables.
3. Query `schema_templates` for `source_catalog`, `source_schema`, and `archive_base_path`.
4. Add a column to the table above.
