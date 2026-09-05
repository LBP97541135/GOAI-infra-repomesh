STATUS: SUCCESS
SUMMARY: Implemented multi-currency support in quote(): added a currency parameter (ISO 4217 code, default USD) carried on Quote, with currency-specific rounding (zero-decimal currencies like JPY round the payable amount to the nearest whole major unit; standard currencies keep two decimal places). Extended tests/test_quote.py with a MultiCurrencyRoundingTests class covering JPY integer rounding (including discount/shipping/tax scenarios) and a standard-decimal (EUR) currency. All 9 unit tests pass unchanged via the frozen command python scripts/run_tests.py.

DELIVERABLES:
- shared/tasks/ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a/candidate/candidate.bundle
- shared/tasks/ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a/candidate/candidate.diff
- shared/tasks/ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a/candidate/changes.json
- shared/tasks/ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a/candidate/evidence.json
