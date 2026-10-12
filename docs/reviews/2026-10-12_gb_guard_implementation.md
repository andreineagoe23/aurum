# Aurum G-B implementation review

Date: 2026-10-12. G-B is implemented and tested in an isolated Linux checkout and was
delivered to `origin/fix/gb-seal-guard` after the owner's explicit branch-only push approval.
It has not been deployed to the Windows VM or used with real market data.

## Authority and identity

- **Approval:** the owner selected "G-B plus research proposal" at 17:41 Europe/London.
  This authorises the chosen guard, synthetic regression tests, status documentation and
  a public-evidence proposal, not new strategy work or any real-data run.
- **Base:** `d570119ca55bb1685f0c7653db577690819326e3` (`origin/main` at verification).
- **Delivery history:** `fix/gb-seal-guard` carried the two reviewed commits; `origin/main`
  remained at the baseline at that push. The owner subsequently directed all work onto
  `main`; Computer fast-forwarded its isolated local `main` to the reviewed tip, without
  changing commit identities. New work is committed on `main`; remote pushes remain
  separately approved, and VM checkout updates are a separate deployment decision.
- **Implementation commit:** `d1130ee592e54cbc6b915b3379149202f92593db`.
- **Original documentation/research commit:** `6f3ccd5751f01d64d425c13754dfcaa5827e9365`.
- **Commit policy:** no trailers; no amend or rewrite of existing history. The original two
  commits used `Perplexity Computer <computer@localhost>` because the sandbox had no
  configured Git identity; those commits are preserved. Subsequent commits use the
  connected GitHub account's verified login/id with repository-local Git configuration.

## Behaviour and scope

- **Validated lifecycle:** `StrategyConfig.status` accepts `draft`, `frozen`, or `rejected`.
  An omitted field defaults to `draft`; unknown values and explicit null fail validation.
- **Recorded dispositions:** the three research YAML configs are marked `rejected`.
  Spec paths, enabled flags, parameters, strategies and historical specs are preserved.
  Outdated comments in the ORB/VWAP config headers were corrected, not their trading rules.
- **Early refusal:** `pipeline.prepare` checks eligibility before reading the pinned symbol
  snapshot. `sealed.resolve_access` independently checks it before opening the journal,
  covering direct access-resolver callers.
- **Opening conditions:** a research family, explicit `frozen` status and a non-empty spec
  path are all necessary. A spec path or `enabled: true` alone cannot bypass disposition.
  Existing date, attribution, journalling and once-per-strategy/variant conditions remain.
- **Reference and parity:** a reference strategy never becomes a research-opening candidate
  through its lifecycle status. The separate parity exemption is unchanged; contradictory
  `--parity --open-sealed` flags now fail before the snapshot, with the existing error text.
- **Limits:** ordinary development and grid requests are unchanged. The broader grid ban
  in the process-lessons proposal was not adopted. A config status is not a signed approval
  registry and does not protect against deliberately changing the code/config.

G-B's source and config changes necessarily change evaluation-context hashes. No historical
batch was reopened, no registry or ledger schema changed, and no historical artefact was
re-hashed, rewritten or rescored.

## Verification

The environment used Python 3.12.13 with the repository's locked dependencies, installed
using `uv sync --locked --dev --python 3.12`. This was the isolated Linux checkout, not the
collector's VM or its dev/release virtual environment; `pyproject.toml` and `uv.lock`
are unchanged.

| Check | Result |
|---|---|
| Baseline offline suite | 411 passed; 11 MT5 tests deselected; 92.31% combined coverage |
| Final offline suite | 412 passed; 11 MT5 tests deselected; 92.32% combined coverage |
| Final statements / missed | 7,409 / 430 |
| Final branches / partial | 1,710 / 186 |
| Ruff lint | Passed |
| Ruff formatting check | Passed |
| Strict mypy | Passed, 95 source files |
| Git whitespace check | Passed |
| Real MT5 / Windows / macOS verification | Not performed in this session |
| Research trials / real tester parity | Not performed; N remains 66 |

One new named safety test contains the refusal case table, rather than adding separate
tests for every input. Existing settings validation and one-time-opening tests were extended;
the latter freezes a private temporary synthetic config, never a repository strategy.

- **Rejected families:** both m15 and m5 are checked through the CLI and direct resolver.
- **No side effects:** snapshot, market-planning, market-reading and ledger constructors are
  replaced with test failures; rejected requests do not create the data or run directory.
- **Bypass cases:** draft/missing status, `enabled: true`, frozen-without-spec, reference
  lifecycle values and contradictory mode flags.
- **Existing guarantees:** the full offline suite retains one-time opening/journalling,
  synthetic reference parity, sealed range refusal, golden replay, risk, architecture,
  fault-injection and reproducibility coverage.

An intermediate full-suite run found an error-message compatibility regression in the
combined-flags path. The implementation restored the original "separate paths; use one"
wording, without editing the existing parity test or weakening its assertion; the final
full suite then passed.

Commands used for the final checks:

```sh
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
AURUM_DATA_DIR=$(mktemp -d /tmp/aurum-synthetic-lean.XXXXXX) \
  AURUM_MODE=BACKTEST AURUM_MT5_ORDER_TESTS=0 PYTHONHASHSEED=0 TZ=UTC \
  uv run --no-sync pytest -m 'not mt5' \
  --cov=aurum --cov-branch --cov-report=term \
  --cov-precision=2 --cov-fail-under=91 -q
git diff --check
```

These are offline/synthetic tests, including fixture-only simulations, fake broker calls
and temporary ledgers. Existing test fixtures may use historical calendar labels, but no
pre-existing real bars, ticks, snapshots, reserve/sealed data or production ledger were read.

## Changed-file inventory

| File | Purpose |
|---|---|
| `src/aurum/settings.py` | Validated lifecycle field, default draft |
| `src/aurum/backtest/sealed.py` | Shared G-B eligibility guard and direct-resolver check |
| `src/aurum/backtest/pipeline.py` | Pre-snapshot refusal, including incompatible flags |
| `configs/strategies/trend_pullback.yaml` | Rejected disposition |
| `configs/strategies/opening_range_breakout.yaml` | Rejected disposition and stale-comment correction |
| `configs/strategies/vwap_reversion.yaml` | Rejected disposition and stale-comment correction |
| `tests/unit/test_settings.py` | Lifecycle validation/default assertions |
| `tests/unit/test_backtest_cli.py` | Named refusal test and private frozen synthetic fixture |
| `docs/ARCHITECTURE.md` | Guard flow and scope |
| `docs/VALIDATION_PROTOCOL.md` | G-B opening conditions; no gate/metric change |
| `docs/STATUS.md` | Owner authorisation, local outcome, unchanged boundaries |
| `docs/proposals/2026-10-12_intraday_gold_research_protocol.md` | Public-evidence proposal, not adopted |
| `docs/reviews/2026-10-12_gb_guard_implementation.md` | This review and verification record |

## Explicitly unchanged and next boundary

The collector, VM_SETUP, release/logon tasks, S2a, S2b, CRLF identity, risk/cost settings,
strategy implementations/specs/registry, production ledger, market-data processing and sealed
period were not changed. The existing process-lessons proposal remains unchanged and
unadopted, as does the new research proposal.

Phase 3 remains "done except tester parity"; Phase 5 remains closed, all three families
rejected, with no deployable strategy and N = 66. No fourth family, rescue revision, ML,
real-data backtest, parity run, reserve processing or deployment is authorised.

Next: owner review, separate approval for each push to `main`, and any later deployment.
The owner can delegate the outstanding MT5 tester-parity work to Claude on the Windows VM,
but that requires an exact run/data-read plan and its own approval; passing these synthetic
tests does not close it.

## Windows follow-up review

The owner supplied Claude's VM inspection and a proposed isolated Windows test plan.
Those observations are attributed to that VM reviewer; Computer has not inspected VM
files, run Windows tests, or accessed MT5.

- **Request A:** recommend a separate checkout and venv of the accepted `main` commit,
  with empty test data and all MT5/order tests excluded. The process must override the
  inherited `UV_PROJECT_ENVIRONMENT`, clear inherited Aurum/MT5 settings (including
  `AURUM_CONFIG_DIR`), then set only the intended test settings. These are process-local
  changes, not changes to user/machine environment variables or the collector.
- **Fail-fast execution:** the supplied semicolon-separated PowerShell native commands
  need explicit exit-code checks; a printed import path must also be an enforced assertion.
  Stop on the first clone, environment, interpreter, import, lint, type or test failure;
  never fall back to the collector's dev venv.
- **Request B:** still deferred. A successful Windows synthetic run does not authorise
  the tester, historical-run artefact reads, history downloads, heartbeat reads, account
  selection, or a phase-gate update.
- **Tester endpoint clarification:** MetaQuotes documents an inclusive start date and
  exclusive end date at midnight, plus automatic history downloads before the requested
  start; therefore uncertainty about endpoint inclusivity is not a reason to grant a
  reserve-data exception ([MetaTrader 5, Strategy Testing](https://www.metatrader5.com/en/terminal/help/algotrading/testing)).
  Any later plan must still reconcile server/UTC bounds and the full history-download
  scope before asking for specific read approval.
- **Collector isolation:** main-only development does not mean pulling into the active
  editable checkout. Unchanged dependency files alone do not establish that replacing
  source files beneath a running process is safe.

No parity procedure, strategy spec, EA, code, config or registered tolerance was changed
as part of this follow-up review. The two proposals remain unadopted.
