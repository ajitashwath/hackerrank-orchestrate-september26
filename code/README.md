# Buy or Wait? Solution
Deterministic financial decision agent. 

## Setup
Requires Python 3.11+ (no third-party packages).

```bash
python --version
```

## Run
From the repository root (folder containing `dataset/` and `code/`):

```bash
python code/main.py
```

Reads `dataset/` (`requests.csv`, `financial_profiles.csv`, `financial_events.csv`,
`request_payment_options.csv`, `messages.csv`, `exchange_rates.csv`) and writes
`output.csv` in the repository root (250 rows + header, exact required columns/order).

If unzipped from `code.zip` standalone, run:
```bash
python main.py
```

## Validate
```bash
python code/evaluation/main.py
```

Checks columns/order, row coverage, `0 <= amount_safe_to_pay <= requested_amount`,
partial-plan (2 payments, sums to requested), installment plans match a supplied
payment option, spending changes reference flexible events, and
`earliest_date_for_full_payment` consistency.

## Approach (summary)
- Starting balance = `current_available_balance`; 90-day daily forecast.
- Pending debits reserved; pending credits / bonuses / commissions / refunds /
  lottery / unrealized gains ignored until settled; confirmed salary counted on
  settlement date; failed/cancelled/unrealized/non-cash excluded.
- FX via settlement-date `exchange_rates.csv` row (latest prior, reverse-pair
  invert, USD/EUR cross fallback).
- Recurring expenses forecast when history (≥3 occurrences) supports weekly /
  biweekly / monthly / quarterly cadence; debits use max (conservative), salary
  credits use min or message-confirmed override; rent +12% and employment-end
  directives from messages applied; bank transfer double-entries excluded.
- `amount_safe_to_pay` = min future surplus capped to requested;
  `earliest_date_for_full_payment` = first date full payment passes the safety check.
- Candidates ranked: completes by `desired_completion_date`, no spending changes,
  min total paid, earlier start, fewer payments, lowest `payment_option_id`.
- Spending changes: up to 3 greedy savings from flexible recurring events in
  user-permitted categories.

## Token usage
See `evaluation/usage_report.md` (and `code/evaluation/usage_report.md`).
