# Synthetic scenarios

One folder per scenario: a `scenario.json` and the page files it uses. Everything is invented:
the firm is "Example Firm", every address is on `example.test`, nothing comes from a real firm.

`scenario.json` has four parts:

- `description`: one sentence saying what the scenario shows.
- `firm`: the fields of a `FirmInput`.
- `responses`: for each URL, what the fake transport answers (a file of the folder, an inline
  body and status, a transport error, or a redirect). The format is documented in
  `tests/scenario_loader.py`.
- `expected`: what triage must conclude (status, mode, verdict and, when it matters, the error kind,
  block reason, terms status, evidence kinds, notes and requests).

The scenarios are written from the spec (`docs/triage_contract.md`), not from the code's output.
`tests/test_triage_scenario_fixtures.py` checks that the files are well-formed and firm-neutral.
Real saved pages stay in `tests/local_fixtures/`, which is not in the repository.

To add a scenario: copy the closest folder, change the pages and the `expected` part, and run the tests.
