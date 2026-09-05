# Review candidate `c42e875cd097431264b52f7c051949b0686591f7` for repomesh-e2e-pricing-core

- Review task: `93e1e9c6-d832-40e7-8d39-711bf27c29f6` (assigned to you, the Team Leader)
- Reviews construction attempt: `ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a` (RepoMesh task `b6e0bc59-255a-4edc-a186-47f5eeb3050c`)
- Base commit: `882231dd887688a986b0faec656a90d29141406c`; candidate head: `c42e875cd097431264b52f7c051949b0686591f7` (exactly one commit on top of the base)
- Budget: 15 minutes from acknowledgement

## What you are asked to do

You are the first reviewer of a Worker's candidate result. You review; you do not fix, re-implement, or re-run anything.
The RepoMesh verifier re-runs the frozen tests independently after your verdict, so your `ACCEPT` means
"good enough to enter independent verification", not "task complete".

This task is completed **through the task protocol**: acknowledge it with `taskflow(action="ack_task")` and
return your verdict with `taskflow(action="submit_task")` as described below. `ack_task` and `submit_task` are
allowed for a Leader on a task that is assigned to the Leader, which this one is (`meta.json.assigned_to` is you).
Do not use `delegate_task`, do not create a project, and do not @mention the Worker.

## Frozen task the Worker had to implement

Modify quote() to accept a mandatory currency parameter (ISO 4217 code). Implement currency-specific rounding: for zero-decimal currencies (e.g., JPY), round the amount to the nearest integer; for other currencies, use standard decimal places. Update all unit tests to cover multi-currency scenarios, including JPY and a standard decimal currency, and ensure existing tests pass.

Frozen acceptance criteria:

- Code compiles without errors.
- Existing tests pass: every test in `tests/test_quote.py` as shipped at the base commit passes unchanged.
- The frozen test command `python scripts/run_tests.py` exits 0.
- Only files under `src/**`, `tests/**`, `README.md` may change; `.github/**` must not change.

## Candidate summary

Changed files (status, path):

- `M` `src/pricing_core/quote.py`
- `M` `tests/test_quote.py`

Worker-reported test evidence (`candidate/evidence.json`, last lines of each command):

- `python scripts/run_tests.py` -> exit 0

  ```text
  test_zero_decimal_currency_rounds_to_nearest_integer (test_quote.MultiCurrencyRoundingTests.test_zero_decimal_currency_rounds_to_nearest_integer) ... ok
  test_zero_decimal_rounding_survives_discount_and_shipping (test_quote.MultiCurrencyRoundingTests.test_zero_decimal_rounding_survives_discount_and_shipping) ... ok
  test_discount_applies_to_merchandise_only (test_quote.QuoteAmountTests.test_discount_applies_to_merchandise_only) ... ok
  test_merchandise_and_shipping (test_quote.QuoteAmountTests.test_merchandise_and_shipping) ... ok
  test_tax_applies_to_the_payable_amount (test_quote.QuoteAmountTests.test_tax_applies_to_the_payable_amount) ... ok
  test_quote_defaults_to_usd (test_quote.QuoteCurrencyTests.test_quote_defaults_to_usd) ... ok
  test_quote_reports_the_requested_currency (test_quote.QuoteCurrencyTests.test_quote_reports_the_requested_currency) ... ok
  
  ----------------------------------------------------------------------
  Ran 9 tests in 0.001s
  
  OK
  ```

## Review checklist

1. Does the diff implement the frozen task (currency parameter, zero-decimal rounding, tests for JPY and a decimal currency)?
2. Do the changed paths stay inside the allowed paths?
3. Is the evidence consistent with the diff (the tests that were run actually exercise the change)?
4. Anything that would make the candidate unsafe to verify or merge (deleted tests, weakened assertions, unrelated changes)?

The full diff is embedded below and is also available in this task directory as `review/candidate.diff`,
`review/changes.json`, `review/evidence.json`.

## How to answer

1. `taskflow(action="ack_task", payload={"taskId": "93e1e9c6-d832-40e7-8d39-711bf27c29f6"})`
2. Read the diff and evidence (below, or the files under `review/`).
3. Submit exactly one verdict:

       taskflow(action="submit_task", payload={
         "taskId": "93e1e9c6-d832-40e7-8d39-711bf27c29f6",
         "status": "<SUCCESS | REVISION_NEEDED | BLOCKED>",
         "summary": "VERDICT: <ACCEPT | REVISION | BLOCKED>\n<your reasons, 2-6 lines>",
         "deliverables": []
       })

   Mapping (fixed): `SUCCESS` = `ACCEPT` (send to independent verification); `SUCCESS_WITH_NOTES` = `ACCEPT` with remarks;
   `REVISION_NEEDED` = `REVISION` (a new construction attempt will be opened with your reasons);
   `BLOCKED` = `BLOCKED` (you cannot judge; say why).
   The first line of `summary` must be `VERDICT: ...`.
4. Then, in this room, reply with one line: `REVIEW_DONE: 93e1e9c6-d832-40e7-8d39-711bf27c29f6 - VERDICT: <...>`.

Do not modify any file in this task directory except through `submit_task`. Do not run the tests yourself; the verifier does that.

## Candidate diff (`base..head`)

```diff
diff --git a/src/pricing_core/quote.py b/src/pricing_core/quote.py
index 5776101..9862ecd 100644
--- a/src/pricing_core/quote.py
+++ b/src/pricing_core/quote.py
@@ -2,6 +2,12 @@
 
 from dataclasses import dataclass
 
+# ISO 4217 currencies with no minor (cent) units. Amounts are quoted in whole
+# major units and must therefore be rounded to the nearest integer.
+_ZERO_DECIMAL_CURRENCIES = frozenset({"JPY", "KRW", "VND"})
+
+_DEFAULT_CURRENCY = "USD"
+
 
 @dataclass(frozen=True)
 class LineItem:
@@ -17,6 +23,7 @@ class LineItem:
 @dataclass(frozen=True)
 class Quote:
     amount: float
+    currency: str
 
 
 def quote(
@@ -24,14 +31,24 @@ def quote(
     shipping: float = 0.0,
     discount_rate: float = 0.0,
     tax_rate: float = 0.0,
+    currency: str = _DEFAULT_CURRENCY,
 ) -> Quote:
     """Price a basket.
 
     Discounts apply to merchandise only; shipping joins afterwards and the whole
-    payable amount is taxed.
+    payable amount is taxed. ``currency`` is an ISO 4217 code (default ``USD``)
+    that is carried on the returned ``Quote``. For zero-decimal currencies (for
+    example JPY, KRW, VND) the payable amount is rounded to the nearest whole
+    major unit; every other currency is rounded to the standard two decimal
+    places.
     """
 
     merchandise = sum(item.amount for item in items)
     payable = merchandise * (1 - discount_rate) + shipping
     payable = payable * (1 + tax_rate)
-    return Quote(amount=round(payable, 2))
+
+    if currency.upper() in _ZERO_DECIMAL_CURRENCIES:
+        amount = float(round(payable))
+    else:
+        amount = round(payable, 2)
+    return Quote(amount=amount, currency=currency)
diff --git a/tests/test_quote.py b/tests/test_quote.py
index 2ec41b4..a13b621 100644
--- a/tests/test_quote.py
+++ b/tests/test_quote.py
@@ -34,5 +34,35 @@ class QuoteCurrencyTests(unittest.TestCase):
         self.assertEqual(result.currency, "USD")
 
 
+class MultiCurrencyRoundingTests(unittest.TestCase):
+    """Currency-specific rounding: zero-decimal vs standard decimal place."""
+
+    def test_zero_decimal_currency_rounds_to_nearest_integer(self) -> None:
+        result = quote([LineItem("desk", 199.99, 1)], currency="JPY")
+        self.assertEqual(result.currency, "JPY")
+        self.assertEqual(result.amount, 200.0)
+
+    def test_zero_decimal_currency_rounds_down_when_fraction_is_low(self) -> None:
+        result = quote([LineItem("desk", 200.4, 1)], tax_rate=0.1, currency="JPY")
+        # 200.4 * 1.1 = 220.44 -> whole major unit 220.0
+        self.assertEqual(result.amount, 220.0)
+
+    def test_standard_decimal_currency_keeps_two_places(self) -> None:
+        result = quote([LineItem("desk", 199.99, 1)], tax_rate=0.08, currency="EUR")
+        # 199.99 * 1.08 = 215.9892 -> 215.99
+        self.assertEqual(result.amount, 215.99)
+        self.assertEqual(result.currency, "EUR")
+
+    def test_zero_decimal_rounding_survives_discount_and_shipping(self) -> None:
+        result = quote(
+            [LineItem("desk", 199.99, 1)],
+            shipping=10.0,
+            discount_rate=0.1,
+            currency="JPY",
+        )
+        # (199.99 * 0.9) + 10 = 189.991 -> whole major unit 190.0
+        self.assertEqual(result.amount, 190.0)
+
+
 if __name__ == "__main__":
     unittest.main()

```
