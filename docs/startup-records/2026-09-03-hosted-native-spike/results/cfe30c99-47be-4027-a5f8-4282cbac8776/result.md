STATUS: SUCCESS
SUMMARY: Made quote() multi-currency in src/pricing_core/quote.py: added a currency parameter (ISO 4217 code, default 'USD') that is validated to exactly three ASCII letters (case-insensitive; anything else raises ValueError), normalised and stored upper-case on Quote.currency; for zero-decimal currencies (JPY/KRW/VND) the payable amount rounds to the nearest whole major unit, otherwise to two decimal places. Extended tests/test_quote.py with CurrencyValidationTests (short/long/digit/empty + lowercase/mixed-case normalisation) and MultiCurrencyRoundingTests (JPY integer rounding incl. tax/discount/shipping and EUR two-decimal). Also documented the currency contract in README.md. All 16 unit tests (6 frozen + 10 new) pass unchanged via the frozen command python scripts/run_tests.py (exit 0).

DELIVERABLES:
- shared/tasks/cfe30c99-47be-4027-a5f8-4282cbac8776/candidate/candidate.bundle
- shared/tasks/cfe30c99-47be-4027-a5f8-4282cbac8776/candidate/candidate.diff
- shared/tasks/cfe30c99-47be-4027-a5f8-4282cbac8776/candidate/changes.json
- shared/tasks/cfe30c99-47be-4027-a5f8-4282cbac8776/candidate/evidence.json
