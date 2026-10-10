# Validation and recovery record

11 October 2026. Local runtime: macOS ARM64, Python 3.14.3,
Home Assistant Core 2026.10.0. Tests use isolated temporary HA configuration
directories and fake physical devices; no installation lights were controlled.

## State preserved after the reported crash

The repository contained complete architecture/state specifications, the custom
integration modules, config/options flows, translations, services metadata,
HACS configuration, installation/configuration README, license, CI workflow and
tests. Existing successful results were 17 engine tests and 10 HA runtime tests.
The pytest failure cache was empty. These files were retained; implementation
was not restarted.

The macOS report `Python-2026-10-11-005358.ips` records SIGSEGV/EXC_BAD_ACCESS
at 00:53:57 EEST in interpreter finalization. Its stack includes
`dictkeys_decref`, `dict_dealloc`, garbage collection and `_Py_Finalize`.
This establishes where the fault occurred, not its underlying cause. Subsequent
test processes exited successfully; the crash has not been reproduced. No
interpreter or environment workaround was introduced.

## Completed validation

```text
.venv/bin/python -X faulthandler -m pytest -q
35 passed, 5 warnings in 0.45s; exit code 0

.venv/bin/ruff check .
All checks passed!

.venv/bin/ruff format --check .
14 files already formatted
```

The warnings originate in HA's aiohttp application subclass and its backoff
dependency's deprecated asyncio calls. No project warnings were reported.

Before preparing the v0.1.0 Git baseline, the same suite was rerun successfully:
35 passed, five dependency warnings, exit code 0. Ruff lint and formatting checks
passed. Checksums verified that integration Python files, tests and
`ARCHITECTURE.md` were unchanged. Repository publication metadata and exclusions
were updated; source/documentation files were inspected for credential patterns
and machine-specific paths before staging.

Coverage includes all twelve requested scenarios, plus actual LightEntity power
and brightness service handling, native targeted master actions, mixed RGB and
on/off members, context propagation, multiple groups, stable identity after
options reload, rejection of nested groups, device quantization/delayed reports,
timeouts, partial failures, and invalid master input. A fresh HA instance loads
the saved master and off target from disk. Startup does not command physical
lights. Input and restoration data stay bounded.

## Remaining deployment work

- Repository metadata now identifies `comache/jai-lighting`, verified as private
  and writable through the connected GitHub plugin, and maintainer `@comache`.
  The manifest remains `0.1.0`. HACS layout is present; private distribution uses
  manual installation because HACS requires public repositories.
- Install and validate with a small physical group on HA OS 18.3. The OS image,
  real device reporting latency and bistrot network have not been tested here.
- Nested groups remain disabled as specified in the v1 architecture.

The GitHub repository was created separately by the owner. No GitHub Release,
Home Assistant deployment or live lighting configuration was created or changed
during development.
