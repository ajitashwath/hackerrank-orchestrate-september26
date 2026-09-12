import csv
import os
import sys
from datetime import date

REQUIRED_COLS = ["request_id", "amount_safe_to_pay", "affordability_status", "recommended_payment_method", "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed", "decision_explanation"]
STATUSES = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
METHODS = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}

def main():
    out_path = "output.csv"
    ds = "dataset"
    for i, a in enumerate(sys.argv[1:]):
        if a == "--output" and i + 1 < len(sys.argv[1:]):
            out_path = sys.argv[1:][i + 1]
        if a == "--dataset" and i + 1 < len(sys.argv[1:]):
            ds = sys.argv[1:][i + 1]
    errors = []

    def err(msg):
        errors.append(msg)

    try:
        with open(out_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != REQUIRED_COLS:
                err(f"columns/order mismatch: {reader.fieldnames}")
            rows = list(reader)
    except FileNotFoundError:
        print(f"missing {out_path}")
        return 1

    try:
        reqs = {r["request_id"]: r for r in csv.DictReader(open(os.path.join(ds, "requests.csv"), encoding="utf-8"))}
        opts = {}
        for r in csv.DictReader(open(os.path.join(ds, "request_payment_options.csv"), encoding="utf-8")):
            opts.setdefault(r["request_id"], []).append(r)
    except FileNotFoundError as e:
        err(str(e))
        reqs, opts = {}, {}

    if len(rows) != len(reqs):
        err(f"row count {len(rows)} != requests {len(reqs)}")
    seen = set()
    for o in rows:
        rid = o.get("request_id", "")
        if rid in seen:
            err(f"duplicate {rid}")
        seen.add(rid)
        q = reqs.get(rid)
        if not q:
            err(f"unknown request {rid}")
            continue
        try:
            req_amt = float(q["requested_amount"])
            safe = float(o["amount_safe_to_pay"])
        except ValueError:
            err(f"{rid}: non-numeric amounts")
            continue
        if not (0 - 1e-6 <= safe <= req_amt + 1e-6):
            err(f"{rid}: safe {safe} out of [0,{req_amt}]")
        if o["affordability_status"] not in STATUSES:
            err(f"{rid}: bad status")
        if o["recommended_payment_method"] not in METHODS:
            err(f"{rid}: bad method")
        if o["affordability_status"] == "affordable_now" and o["earliest_date_for_full_payment"] != q["request_date"]:
            err(f"{rid}: affordable_now earliest must equal request_date")
        if o["recommended_payment_method"] == "partial_payment":
            parts = o["payment_plan"].split("|")
            if len(parts) != 2:
                err(f"{rid}: partial must have exactly 2 payments")
            else:
                try:
                    a1 = float(parts[0].split(":")[1])
                    a2 = float(parts[1].split(":")[1])
                    if abs(a1 + a2 - req_amt) > 0.03:
                        err(f"{rid}: partial sum {a1 + a2} != {req_amt}")
                except (IndexError, ValueError):
                    err(f"{rid}: malformed partial plan")
        if o["recommended_payment_method"] == "installments":
            ok = False
            for op in opts.get(rid, []):
                if op["payment_method"] != "installments":
                    continue
                n = int(float(op["number_of_payments"]))
                from datetime import timedelta
                first = date.fromisoformat(op["first_payment_date"])
                freq = int(round(float(op["payment_frequency_days"] or 30)))
                per = float(op["payment_amount"])
                exp = "|".join(f"{(first + timedelta(days=freq * i)).isoformat()}:" + f"{round(per, 2)}" for i in range(n))
                norm = lambda p: "|".join(d + ":" + str(round(float(a), 2)) for d, a in (x.split(":") for x in p.split("|")))
                if norm(exp) == norm(o["payment_plan"]):
                    ok = True
                    break
            if not ok:
                err(f"{rid}: installment plan matches no option")
        chg = o.get("spending_changes_needed", "")
        if chg not in ("none", ""):
            if len(chg.split("|")) > 3:
                err(f"{rid}: >3 spending changes")

    if errors:
        print(f"FAIL: {len(errors)} issue(s)")
        for e in errors[:20]:
            print(" -", e)
        return 1
    print(f"OK: {len(rows)} rows pass contract checks")
    return 0

if __name__ == "__main__":
    sys.exit(main())
