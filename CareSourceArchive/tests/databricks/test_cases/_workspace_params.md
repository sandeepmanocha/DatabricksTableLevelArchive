# Workspace Parameters

Before running any test, **ask the user** for:

| Parameter | Example | Current |
|---|---|---|
| `PROFILE` | `DEFAULT`, `fe-sandbox-manocha` | `fe-sandbox-manocha` |
| `TARGET` | `dev`, `dev-serverless` | `dev-serverless` |
| `SOURCE_CATALOG` | `sandeep_manocha` | `dev2_archive` |
| `SOURCE_SCHEMA` | `source_data_samples` | `source_data_samples` |

All other values derive from these (config tables live under `<SOURCE_CATALOG>.caresource_audit` or a metadata schema the user specifies).

## Quick-reference: bundle deploy & run

```bash
databricks bundle deploy -t <TARGET> --profile <PROFILE>

databricks bundle run <RESOURCE> -t <TARGET> --profile <PROFILE> \
  --params key1=val1 \
  --params key2=val2
```

> **CLI quoting note:** Pass each parameter as a separate `--params` flag. Values containing commas (e.g. `years`) must use inner quotes: `--params 'years="2020,2021"'`. The notebook strips quote artifacts.
