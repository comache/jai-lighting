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

## Preserved v0.1.0 validation

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

## Historical v0.1.0 deployment notes

- Repository metadata now identifies `comache/jai-lighting`, verified as private
  and writable through the connected GitHub plugin, and maintainer `@comache`.
  The manifest remains `0.1.0`. HACS layout is present; private distribution uses
  manual installation because HACS requires public repositories.
- Install and validate with a small physical group on HA OS 18.3. The OS image,
  real device reporting latency and installation network have not been tested here.
- Nested groups remain disabled as specified in the v1 architecture.

The GitHub repository was created separately by the owner. No GitHub Release,
Home Assistant deployment or live lighting configuration was created or changed
during development.

## v0.2 development implementation validation

11 October 2026. Approved implementation on `v0.2-development`; manifest version
`0.2.0.dev0`, pinned HA Core 2026.10.0 and Python 3.14.3. No live deployment or
hardware control occurred. Final source state is recorded in the HA-layer commit
`d49b4b0`, diagnostics correction `01442f1` and the following documentation checkpoint.

```text
.venv/bin/python -X faulthandler -m pytest -q
113 passed, 5 warnings in 64.57s; exit code 0

.venv/bin/ruff check .
All checks passed!; exit code 0

.venv/bin/ruff format --check .
35 files already formatted; exit code 0
```

The same five dependency deprecations remain; no interpreter crash occurred.
The original arithmetic/reference regressions remain. Intentional v0.2 contract
changes are covered by hierarchy/primary-owner migration tests and the preview
confirmation step, rather than by weakening numerical or failure assertions.

Added pure tests cover topology/identity validation, 1,500-level iterative
traversal, independent faders with shared leaf memory, OFF/FULL/RESTORE behavior,
mixed capabilities, property preservation, preset specificity and source tracking,
future/corrupt storage guards and migration owner-selected targets.

Real HA runtime tests cover native group/preset creation and editing, inline
recipe repair, provisional-ID remapping, coherent strict batches, native scenes,
all supported color families, effects, context, partial failure, cancellation,
storage failure before/after dispatch, registry rename/deletion/reuse, deletion
safeguards, journal interruptions and legacy overlap repair. Fresh HA instances
load independent masters and shared OFF targets from disk without startup
dispatch. Fake physical devices record all commands; migration/configuration
tests confirm no hardware commands.

Repository checks found no tracked environments, caches, crash reports, local HA
data, secrets, machine-specific paths or real venue/device configuration. Tests
use synthetic identities. Existing `.gitignore` exclusions remain in force for
virtual environments, Python/Ruff/pytest caches, local HA directories and storage,
editor/OS files, credentials, crash reports and build/coverage artifacts.

Preserved annotated v0.1.0 tag object:
`f1028297e2857692e5955b53437157c3597653ce`.
Preserved target commit and stable `main`:
`5f6769b2ea8398e2786a9db2b4cee7593917cbec`.
No tag was created/moved/deleted, branch merged, repository visibility changed,
GitHub Release created or remote development push performed.

The two accepted native HA boundaries remain. Future HA versions need journal
host-storage/API revalidation. Actual hardware timing, device gamut, production
frontend rendering and HA OS behavior are not verified by these simulated tests.
See `IMPLEMENTATION_V0.2.md` for subsystem details, recovery limits and the
recommended first hardware review using ONE small physical group.
