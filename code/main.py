#!/usr/bin/env python3
"""Buy or Wait? deterministic financial decision agent.

Reads dataset/ and writes output.csv in repo root.
Stdlib only, deterministic, no network, no LLM calls.
Implements 90-day safety check per problem_statement.md.
"""
import csv
import os
import re
from datetime import date, datetime, timedelta
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET = os.path.join(REPO_ROOT, "dataset")
OUTPUT_PATH = os.path.join(REPO_ROOT, "output.csv")

FORECAST_DAYS = 90

AMOUNT_RE = re.compile(r"(IDR|INR|ZAR|EUR|USD)\s*([\d][\d,\.]*)")
ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None

def parse_dt(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.fromisoformat(s[:10])
        except Exception:
            return None

def parse_float(s):
    s = (s or "").strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None

def fmt_amount(x):
    x = round(float(x) + 1e-9, 2)
    if x < 0 and x > -0.005:
        x = 0.0
    s = f"{x:.2f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s else "0"

def load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

# ---------------- FX ----------------
class FX:
    def __init__(self, rows):
        # rows: rate_date,from_currency,to_currency,rate
        self.by_pair = defaultdict(list)  # (from,to) -> sorted [(date,rate)]
        for r in rows:
            d = parse_date(r.get("rate_date"))
            f = (r.get("from_currency") or "").strip()
            t = (r.get("to_currency") or "").strip()
            v = parse_float(r.get("rate"))
            if d and f and t and v:
                self.by_pair[(f, t)].append((d, v))
        for k in self.by_pair:
            self.by_pair[k].sort()

    def rate_for(self, frm, to, settle):
        if frm == to:
            return 1.0
        if settle is None:
            settle = date(2025, 1, 1)
        # exact pair, latest rate_date <= settle
        lst = self.by_pair.get((frm, to), [])
        best = None
        for d, v in lst:
            if d <= settle:
                best = v
            else:
                break
        if best is not None:
            return best
        if lst:
            return lst[0][1]
        # reverse pair
        lst2 = self.by_pair.get((to, frm), [])
        if lst2:
            b2 = None
            for d, v in lst2:
                if d <= settle:
                    b2 = v
                else:
                    break
            if b2 is None:
                b2 = lst2[0][1]
            if b2:
                return 1.0 / b2
        # cross via USD
        for mid in ("USD", "EUR"):
            if mid in (frm, to):
                continue
            try:
                r1 = self.rate_for(frm, mid, settle)
                r2 = self.rate_for(mid, to, settle)
                # only use if both legs were real (not fallback 1.0 loop) - avoid recursion loop
                return r1 * r2
            except Exception:
                continue
        return 1.0

    def convert(self, amount, frm, to, settle):
        if amount is None:
            return None
        if frm == to or frm == "" or to == "":
            return float(amount)
        return float(amount) * self.rate_for(frm, to, settle)

# ---------------- Messages ----------------
def analyze_messages(msgs):
    """Per-user message directives. Returns dict user_id -> directives."""
    info = defaultdict(lambda: {
        "salary_override": None,  # (amount_home?, amount_raw, currency, effective_date, sent_at)
        "salary_amounts": [],  # list of (sent_at, amount, currency, text_snippet)
        "employment_ended": None,  # date
        "rent_multiplier": 1.0,
        "rent_mult_date": None,
        "transfer_exclusion": False,
        "salary_dates": [],  # revised salary dates
    })
    for m in msgs:
        uid = m.get("user_id", "")
        text = m.get("message_text", "") or ""
        src = (m.get("source_type", "") or "").lower()
        sent = parse_dt(m.get("sent_at", ""))
        sent_d = sent.date() if isinstance(sent, datetime) else parse_date(m.get("sent_at", ""))
        low = text.lower()
        d = info[uid]
        if "matching debit and credit" in low or "transfer between your two accounts" in low or "same account holder" in low:
            d["transfer_exclusion"] = True
        if "increases monthly rent by 12%" in low or "increased monthly rent" in low or "rent by 12%" in low:
            d["rent_multiplier"] = 1.12
            d["rent_mult_date"] = sent_d
        # employment ended
        if any(p in low for p in ["seasonal contract has ended", "employment has ended", "employment ended", "no off-season income", "no renewal has been confirmed"]):
            if sent_d and (d["employment_ended"] is None or sent_d < d["employment_ended"]):
                d["employment_ended"] = sent_d
        if src == "employer":
            # skip pending bonus/commission-only messages (no confirmed salary amount to apply)
            is_pending_only = any(p in low for p in ["still pending", "pending review", "not been approved", "not approved", "awaiting", "has not been approved", "until the payout is closed", "can change until"])
            # extract currency amounts
            amts = []
            for cur, num in AMOUNT_RE.findall(text):
                try:
                    v = float(num.replace(",", ""))
                    amts.append((cur, v))
                except Exception:
                    pass
            dates = ISO_DATE_RE.findall(text)
            if amts and ("salar" in low or "pay" in low or "gaji" in low or "penggajian" in low):
                # pick amount: prefer the one near salary keywords; fallback largest
                # heuristic: if multiple, pick first salary-adjacent; else max
                chosen = max(amts, key=lambda x: x[1]) if len(amts) > 1 else amts[0]
                # effective date: explicit ISO date in text (excluding sent date?) else sent date
                eff = None
                for ds in dates:
                    dd = parse_date(ds)
                    if dd:
                        eff = dd
                        break
                if eff is None:
                    eff = sent_d
                if not is_pending_only:
                    d["salary_amounts"].append((sent_d, chosen[1], chosen[0], text[:80]))
    # collapse salary_amounts to latest before request (done per-request)
    return info

def latest_salary_before(amounts, req_date):
    best = None
    for sent_d, amt, cur, _ in amounts:
        if sent_d and sent_d <= req_date:
            if best is None or sent_d >= best[0]:
                best = (sent_d, amt, cur)
        elif sent_d is None:
            if best is None:
                best = (sent_d, amt, cur)
    return best

# ---------------- Recurring detection ----------------
def detect_recurring(history):
    """history: list of (event_date, amount_home, category, direction).
    Returns list of dicts {category,direction,interval_days,amount,last_date,count}."""
    groups = defaultdict(list)
    for ed, amt, cat, direction in history:
        if ed is None or amt is None:
            continue
        groups[(cat or "", direction or "")].append((ed, amt))
    out = []
    for (cat, direction), items in groups.items():
        if len(items) < 3:
            continue
        items.sort()
        intervals = [(items[i + 1][0] - items[i][0]).days for i in range(len(items) - 1)]
        intervals = [iv for iv in intervals if iv > 0]
        if len(intervals) < 2:
            continue
        med = sorted(intervals)[len(intervals) // 2]
        if 6 <= med <= 8:
            iv = 7
        elif 13 <= med <= 16:
            iv = 14
        elif 27 <= med <= 32:
            iv = 30
        elif 85 <= med <= 95:
            iv = 91
        else:
            continue
        # check regularity: at least half intervals within tolerance
        tol = 3 if iv <= 14 else 5
        if iv == 91:
            tol = 8
        ok = sum(1 for x in intervals if abs(x - iv) <= tol)
        if ok < max(2, len(intervals) // 2):
            continue
        amts = [a for _, a in items[-6:]]
        out.append({
            "category": cat, "direction": direction, "interval": iv,
            "amounts": amts, "last_date": items[-1][0], "count": len(items),
        })
    return out

def conservative_amount(rec, direction):
    amts = rec["amounts"]
    if not amts:
        return 0.0
    if direction == "debit":
        return max(amts)  # conservative: high expense
    return min(amts)  # conservative: low income

SALARY_HINTS = ("salary", "payroll", "gaji", "penggajian", "pay ")

def is_salary_group(cat, items_sample_desc=""):
    c = (cat or "").lower()
    if "salary" in c or "payroll" in c or "income" in c:
        return True
    return False

# ---------------- Forecast & safety ----------------
def build_flows(profile, user_events, msg_info, fx, request_date, home):
    """Returns (daily net dict date->flow, one_time_desc, recurring_used, scheduled_list)."""
    flows = defaultdict(float)
    req = request_date
    end = req + timedelta(days=FORECAST_DAYS)

    # transfer exclusion detection
    exclude_pairs = set()
    if msg_info and msg_info.get("transfer_exclusion"):
        # find debit+credit pairs with same converted amount within 7 days
        debits = [e for e in user_events if e.get("_dir") == "debit" and e.get("_amt_home") is not None]
        credits = [e for e in user_events if e.get("_dir") == "credit" and e.get("_amt_home") is not None]
        for de in debits:
            for ce in credits:
                try:
                    if abs(float(de["_amt_home"]) - float(ce["_amt_home"])) < 0.01:
                        dd = de.get("_settle") or de.get("_event")
                        cd = ce.get("_settle") or ce.get("_event")
                        if dd and cd and abs((dd - cd).days) <= 7:
                            # only exclude if description hints transfer
                            ddesc = ((de.get("description") or "") + " " + (ce.get("description") or "")).lower()
                            if "transfer" in ddesc or True:  # message already confirms; exclude
                                exclude_pairs.add(de.get("event_id"))
                                exclude_pairs.add(ce.get("event_id"))
                except Exception:
                    pass

    history_for_recur = []  # (event_date, amt_home, category, direction)
    scheduled = []  # (settle_date, signed_amt, event_id)
    salary_sched = []

    for e in user_events:
        eid = e.get("event_id")
        if eid in exclude_pairs:
            continue
        status = (e.get("status") or "").lower()
        if status in ("cancelled", "failed", "unrealized"):
            continue
        direction = (e.get("direction") or "").lower()
        if direction == "non_cash":
            continue
        etype = (e.get("event_type") or "").lower()
        if etype == "investment_valuation":
            continue
        amt_home = e.get("_amt_home")
        if amt_home is None:
            continue  # blank amount w/o image: skip (do not invent)
        ev_d = e.get("_event")
        st_d = e.get("_settle") or ev_d
        cat = e.get("category") or ""
        desc = (e.get("description") or "") + " " + (e.get("event_type") or "")
        # pending credits: ignore
        if status == "pending" and direction == "credit":
            continue
        if status == "settled":
            if ev_d and ev_d < req:
                # history candidate (only debits + salary credits for recurrence)
                if direction == "debit" and etype in ("expense", "subscription", "debt_payment", "investment_purchase"):
                    history_for_recur.append((ev_d, amt_home, cat, direction))
                elif direction == "credit" and ("salar" in desc.lower() or "payroll" in desc.lower() or "gaji" in desc.lower() or etype == "income"):
                    # only salary-like income for recurrence; skip bonus/commission/refund/lottery/investment gains
                    dl = desc.lower()
                    if any(k in dl for k in ["bonus", "commission", "refund", "lottery", "investment", "prize", "cashback", "reversal"]):
                        pass
                    else:
                        history_for_recur.append((ev_d, amt_home, cat, direction))
            elif st_d and st_d >= req and st_d <= end:
                # settled with future settlement? rare; treat as scheduled flow
                sgn = -amt_home if direction == "debit" else amt_home
                # pending debits already handled below; settled future = confirmed
                flows[st_d] += sgn
                scheduled.append((st_d, sgn, eid))
            continue
        if status == "pending" and direction == "debit":
            # reserve pending debits on settlement (or event) date if within window
            d = st_d
            if d is None:
                d = req
            if d < req:
                d = req
            if d <= end:
                flows[d] += -amt_home
                scheduled.append((d, -amt_home, eid))
            continue
        if status == "scheduled":
            d = st_d or ev_d or req
            if d < req:
                d = req
            if d <= end:
                sgn = -amt_home if direction == "debit" else amt_home
                # do not count scheduled non-salary credits? commissions/bonuses pending already excluded as pending;
                # scheduled bonuses? be conservative: only count salary-like + refunds? Spec: count confirmed salary.
                if direction == "credit":
                    dl = desc.lower()
                    if ("salar" in dl or "payroll" in dl or "gaji" in dl) or etype == "income":
                        if any(k in dl for k in ["bonus", "commission", "lottery", "prize"]) and "salary" not in dl:
                            continue
                    else:
                        # scheduled non-salary credit (e.g., reimbursement?) - count if not bonus-like?
                        if any(k in dl for k in ["bonus", "commission", "lottery", "prize", "investment"]):
                            continue
                flows[d] += sgn
                scheduled.append((d, sgn, eid))
                if direction == "credit" and ("salar" in desc.lower() or "payroll" in desc.lower()):
                    salary_sched.append((d, amt_home, eid))
            continue

    # recurring forecast
    recurs = detect_recurring(history_for_recur)
    # employment ended: kill salary recurrence
    emp_end = msg_info.get("employment_ended") if msg_info else None
    # salary override from messages
    sal_override = None
    if msg_info and msg_info.get("salary_amounts"):
        lb = latest_salary_before(msg_info["salary_amounts"], req)
        if lb:
            _, amt_raw, cur_raw = lb
            # convert to home at request date
            sal_override = fx.convert(amt_raw, cur_raw, home, req)
    rent_mult = msg_info.get("rent_multiplier", 1.0) if msg_info else 1.0
    rent_mult_date = msg_info.get("rent_mult_date") if msg_info else None

    # scheduled date guard to avoid double counting
    sched_keys = [(d, s) for d, s, _ in scheduled]

    def near_scheduled(d, signed_amt, tol_days=4, tol_amt=0.05):
        for sd, ss in sched_keys:
            if abs((sd - d).days) <= tol_days and abs(ss - signed_amt) / max(1.0, abs(ss)) <= tol_amt + 1e-9:
                return True
        return False

    recur_flows = []
    for rec in recurs:
        cat = rec["category"]
        direction = rec["direction"]
        # income: only salary-like
        if direction == "credit" and not is_salary_group(cat):
            continue
        if emp_end and direction == "credit" and is_salary_group(cat):
            # if employment ended before/during forecast, skip future salary
            pass  # handled per-date below
        amt = conservative_amount(rec, direction)
        if direction == "credit" and is_salary_group(cat) and sal_override is not None:
            amt = sal_override
        # rent increase
        if direction == "debit" and rent_mult != 1.0 and ("rent" in cat.lower() or "housing" in cat.lower()):
            pass  # apply per-date
        # generate dates
        last = rec["last_date"]
        iv = rec["interval"]
        # step forward to (req, end]
        nxt = last + timedelta(days=iv)
        # fast-forward if behind
        while nxt < req:
            nxt += timedelta(days=iv)
        while nxt <= end:
            a = amt
            if direction == "debit" and rent_mult != 1.0 and ("rent" in cat.lower() or "housing" in cat.lower()):
                if rent_mult_date is None or nxt >= rent_mult_date:
                    a = amt * rent_mult
            if direction == "credit" and is_salary_group(cat) and emp_end and nxt > emp_end:
                nxt += timedelta(days=iv)
                continue
            sgn = -a if direction == "debit" else a
            if not near_scheduled(nxt, sgn):
                flows[nxt] += sgn
                recur_flows.append((nxt, sgn, cat))
            nxt += timedelta(days=iv)
    return flows, scheduled, recur_flows

def cumulative_min_check(b0, flows, req, end, minimum, extra_payments):
    """Simulate day by day. extra: list of (date, amount). Returns (ok, min_balance_seen)."""
    pay_by_day = defaultdict(float)
    for d, a in extra_payments:
        pay_by_day[d] += a
    bal = b0
    min_seen = bal
    # check initial day before flows? balance must stay >= min after any expense/payment
    d = req
    # apply req-day flows + payments together; iterate days
    day = req
    while day <= end:
        net = flows.get(day, 0.0) - pay_by_day.get(day, 0.0)
        bal += net
        if bal < min_seen:
            min_seen = bal
        if bal < minimum - 1e-6:
            return False, min_seen
        day += timedelta(days=1)
    return True, min_seen

def base_cumulative(b0, flows, req, end):
    bal = b0
    cum = {}
    day = req
    while day <= end:
        bal += flows.get(day, 0.0)
        cum[day] = bal
        day += timedelta(days=1)
    return cum

def compute_safe_and_earliest(b0, flows, req, end, minimum, requested):
    cum = base_cumulative(b0, flows, req, end)
    # base must hold? if base violates, safe=0
    min_surplus = min((v - minimum) for v in cum.values()) if cum else (b0 - minimum)
    safe = max(0.0, min_surplus)
    safe = min(safe, requested)
    # earliest full: first d where min_{t>=d}(cum[t]) - requested >= minimum and min_{t<d}(cum[t])>=minimum
    days = sorted(cum.keys())
    # prefix minima of base
    earliest = None
    # precompute suffix minima of cum
    suf_min = {}
    s = float("inf")
    for d in reversed(days):
        s = min(s, cum[d])
        suf_min[d] = s
    # prefix min before d
    pre_min = float("inf")
    base_ok_prefix = True
    for d in days:
        if pre_min == float("inf"):
            pre_ok = True
        else:
            pre_ok = pre_min >= minimum - 1e-6
        if pre_ok and suf_min[d] - requested >= minimum - 1e-6:
            earliest = d
            break
        pre_min = cum[d] if pre_min == float("inf") else min(pre_min, cum[d])
    return safe, earliest

def main():
    profiles = {r["user_id"]: r for r in load_csv(os.path.join(DATASET, "financial_profiles.csv"))}
    events = load_csv(os.path.join(DATASET, "financial_events.csv"))
    requests = load_csv(os.path.join(DATASET, "requests.csv"))
    options = load_csv(os.path.join(DATASET, "request_payment_options.csv"))
    messages = load_csv(os.path.join(DATASET, "messages.csv"))
    fx = FX(load_csv(os.path.join(DATASET, "exchange_rates.csv")))
    msg_info = analyze_messages(messages)

    # index events by user with converted amounts (need profile home currency)
    events_by_user = defaultdict(list)
    for e in events:
        uid = e.get("user_id")
        prof = profiles.get(uid)
        home = (prof.get("home_currency") if prof else "") or (e.get("currency") or "")
        amt = parse_float(e.get("amount"))
        cur = (e.get("currency") or "").strip() or home
        st = parse_date(e.get("settlement_date")) or parse_date(e.get("event_date"))
        ev = parse_date(e.get("event_date"))
        conv = None
        if amt is not None:
            conv = fx.convert(amt, cur, home, st or ev or date(2025, 1, 1))
        e["_amt_home"] = conv
        e["_settle"] = parse_date(e.get("settlement_date"))
        e["_event"] = parse_date(e.get("event_date"))
        e["_dir"] = (e.get("direction") or "").lower()
        events_by_user[uid].append(e)

    opts_by_req = defaultdict(list)
    for o in options:
        opts_by_req[o.get("request_id")].append(o)
    for k in opts_by_req:
        opts_by_req[k].sort(key=lambda x: x.get("payment_option_id", ""))

    out_rows = []
    for rq in requests:
        rid = rq.get("request_id")
        uid = rq.get("user_id")
        prof = profiles.get(uid, {})
        home = (prof.get("home_currency") or "").strip()
        b0 = parse_float(prof.get("current_available_balance")) or 0.0
        minimum = parse_float(prof.get("minimum_balance_to_keep")) or 0.0
        req_date = parse_date(rq.get("request_date"))
        des_date = parse_date(rq.get("desired_completion_date"))
        requested = parse_float(rq.get("requested_amount")) or 0.0
        allows_partial = (rq.get("allows_partial_payment") or "").strip().lower() == "true"
        consider = [(s or "").strip() for s in (prof.get("payment_methods_user_will_consider") or "").split("|") if (s or "").strip()]
        max_inst = parse_float(prof.get("max_installment_months"))
        end = req_date + timedelta(days=FORECAST_DAYS)

        uevents = events_by_user.get(uid, [])
        mi = msg_info.get(uid, {"rent_multiplier": 1.0, "rent_mult_date": None, "employment_ended": None, "salary_amounts": [], "transfer_exclusion": False})
        flows, scheduled, recur_flows = build_flows(prof, uevents, mi, fx, req_date, home)

        safe, earliest = compute_safe_and_earliest(b0, flows, req_date, end, minimum, requested)
        safe = round(safe + 1e-9, 2)

        # candidate flexible events for spending changes
        protect = set((prof.get("expense_categories_to_protect") or "").split("|")) if prof.get("expense_categories_to_protect") else set()
        reduce_cats = set((prof.get("expense_categories_user_is_willing_to_reduce") or "").split("|")) if prof.get("expense_categories_user_is_willing_to_reduce") else set()
        stop_cats = set((prof.get("expense_categories_user_is_willing_to_stop") or "").split("|")) if prof.get("expense_categories_user_is_willing_to_stop") else set()
        reduce_cats = {c for c in reduce_cats if c}
        stop_cats = {c for c in stop_cats if c}

        # map recurring debit groups to representative event_id + savings
        flex_cands = []  # (savings90, action_str, category, event_id, new_amt_or_None)
        # gather last settled event per category for id + min_allowed
        last_by_cat = {}
        for e in uevents:
            if (e.get("status") or "").lower() != "settled":
                continue
            if e.get("_dir") != "debit":
                continue
            if (e.get("flexibility") or "").lower() == "fixed":
                continue
            cat = e.get("category") or ""
            if cat in protect:
                continue
            ed = e.get("_event")
            if ed and ed < req_date:
                if cat not in last_by_cat or ed > last_by_cat[cat].get("_event"):
                    last_by_cat[cat] = e
        for rec in detect_recurring([(e, 0, "", "")] if False else []):
            pass
        # use recur groups from history: recompute quickly via flows? Instead derive from last_by_cat + forecast counts
        # count occurrences in forecast per category
        cnt90 = defaultdict(int)
        amt90 = {}
        for d, sgn, cat in recur_flows:
            if sgn < 0:
                cnt90[cat] += 1
                amt90[cat] = max(amt90.get(cat, 0.0), -sgn)
        for cat, rep in last_by_cat.items():
            if cat not in cnt90:
                continue
            flex = (rep.get("flexibility") or "").lower()
            n = cnt90[cat]
            orig = amt90.get(cat, rep.get("_amt_home") or 0.0)
            min_allowed = parse_float(rep.get("minimum_allowed_amount"))
            eid = rep.get("event_id")
            can_stop = ("stoppable" in flex or "stop" in flex) or (cat in stop_cats)
            can_reduce = ("reducib" in flex) or (cat in reduce_cats)
            if can_stop:
                flex_cands.append((orig * n, f"stop:{eid}", cat, eid, None, orig, n))
            if can_reduce:
                new_amt = min_allowed if min_allowed is not None and min_allowed < orig else round(orig * 0.5, 2)
                if new_amt < orig:
                    flex_cands.append(((orig - new_amt) * n, f"reduce_to:{eid}:{fmt_amount(new_amt)}", cat, eid, new_amt, orig, n))
        # dedupe per event: keep best saving per event, then sort desc, take top 3 distinct events
        best_per_event = {}
        for sav, act, cat, eid, new_amt, orig, n in flex_cands:
            if eid not in best_per_event or sav > best_per_event[eid][0]:
                best_per_event[eid] = (sav, act, cat, new_amt, orig, n)
        ranked = sorted(best_per_event.values(), key=lambda x: -x[0])

        def try_with_changes(change_acts):
            # change_acts: list of (eid, new_amt_or_None)
            mod_flows = dict(flows)
            # zero out / reduce forecast flows for those categories
            eid_to_cat = {}
            for sav, act, cat, new_amt, orig, n in ranked:
                # parse eid from act
                m = re.search(r"(event_\d+)", act)
                if m:
                    eid_to_cat[m.group(1)] = (cat, new_amt, orig)
            for ceid, cnew in change_acts:
                cat, _, orig = eid_to_cat.get(ceid, (None, None, None))
                if cat is None:
                    continue
                # adjust recur_flows-derived entries: find days with that category
                for d, sgn, c in recur_flows:
                    if c != cat:
                        continue
                    if cnew is None:
                        mod_flows[d] = mod_flows.get(d, 0.0) - sgn  # sgn negative -> add back
                    else:
                        # reduce: original -sgn assumed orig; new flow = -cnew
                        # delta = (-cnew) - sgn
                        mod_flows[d] = mod_flows.get(d, 0.0) + ((-cnew) - sgn)
            return mod_flows

        # evaluate full with changes if needed
        full_needs_changes = False
        chosen_changes = []
        if not (safe >= requested - 1e-6):
            # greedy try 1..3
            for k in (1, 2, 3):
                if k > len(ranked):
                    break
                # pick top-k distinct events
                picks = []
                acts = []
                for sav, act, cat, new_amt, orig, n in ranked[:k]:
                    m = re.search(r"(event_\d+)", act)
                    eid = m.group(1) if m else None
                    picks.append((eid, new_amt))
                    acts.append(act)
                # distinct events check + stop/reduce same event mutually exclusive (we have one act per event so ok)
                mod = try_with_changes(picks)
                s2, e2 = compute_safe_and_earliest(b0, mod, req_date, end, minimum, requested)
                if s2 >= requested - 1e-6:
                    full_needs_changes = True
                    chosen_changes = acts
                    break

        # build candidates
        cands = []  # (rank_tuple, method, status, plan_str, earliest_for_output, changes_str, total_paid, start_date, npay, opt_id)
        # full option
        full_opts = [o for o in opts_by_req.get(rid, []) if (o.get("payment_method") or "") == "full_payment"]
        full_opt = full_opts[0] if full_opts else None
        if "full_payment" in consider and full_opt:
            if safe >= requested - 1e-6:
                fd = parse_date(full_opt.get("first_payment_date")) or req_date
                plan = f"{fd.isoformat()}:{fmt_amount(requested)}"
                cands.append(((0, 0, requested, fd.toordinal(), 1, full_opt.get("payment_option_id", "")), "full_payment", "affordable_now", plan, req_date, "none", requested, fd, 1, full_opt.get("payment_option_id", "")))
            elif full_needs_changes:
                fd = parse_date(full_opt.get("first_payment_date")) or req_date
                plan = f"{fd.isoformat()}:{fmt_amount(requested)}"
                cands.append(((0, 1, requested, fd.toordinal(), 1, full_opt.get("payment_option_id", "")), "full_payment", "affordable_with_plan", plan, req_date, "|".join(chosen_changes), requested, fd, 1, full_opt.get("payment_option_id", "")))
        # installments
        if "installments" in consider:
            for o in opts_by_req.get(rid, []):
                if (o.get("payment_method") or "") != "installments":
                    continue
                n = int(parse_float(o.get("number_of_payments")) or 0)
                if n <= 0:
                    continue
                if max_inst is not None and n > max_inst + 1e-9:
                    continue
                first = parse_date(o.get("first_payment_date")) or req_date
                freq = parse_float(o.get("payment_frequency_days")) or 30
                per = parse_float(o.get("payment_amount")) or 0.0
                total = parse_float(o.get("total_payable_amount")) or per * n
                paydates = [first + timedelta(days=int(round(freq)) * i) for i in range(n)]
                if paydates[-1] > end + timedelta(days=365 * 2):
                    pass
                # must complete by desired_completion_date? enforce for ranking; skip if exceeds
                if des_date and paydates[-1] > des_date:
                    continue
                extra = [(d, per) for d in paydates]
                ok, _ = cumulative_min_check(b0, flows, req_date, end, minimum, extra)
                if ok:
                    plan = "|".join(f"{d.isoformat()}:{fmt_amount(per)}" for d in paydates)
                    cands.append(((0, 0, total, first.toordinal(), n, o.get("payment_option_id", "")), "installments", "affordable_with_plan", plan, earliest, "none", total, first, n, o.get("payment_option_id", "")))
        # partial
        if allows_partial and "partial_payment" in consider and safe > 1e-6 and safe < requested - 1e-6 and earliest is not None and des_date and earliest <= des_date:
            rem = round(requested - safe, 2)
            extra = [(req_date, safe), (earliest, rem)]
            ok, _ = cumulative_min_check(b0, flows, req_date, end, minimum, extra)
            if ok:
                plan = f"{req_date.isoformat()}:{fmt_amount(safe)}|{earliest.isoformat()}:{fmt_amount(rem)}"
                cands.append(((0, 0, requested, req_date.toordinal(), 2, "zz"), "partial_payment", "affordable_with_plan", plan, earliest, "none", requested, req_date, 2, "zz"))
        # wait
        if "full_payment" in consider and earliest is not None and earliest > req_date:
            # wait plan safe by construction; also must complete by deadline? rank prefers deadline; allow only if earliest <= desired?
            if des_date is None or earliest <= des_date + timedelta(days=0):
                # also verify safety explicitly
                ok, _ = cumulative_min_check(b0, flows, req_date, end, minimum, [(earliest, requested)])
                if ok:
                    plan = f"{earliest.isoformat()}:{fmt_amount(requested)}"
                    cands.append(((1, 0, requested, earliest.toordinal(), 1, "zw"), "wait", "affordable_later", plan, earliest, "none", requested, earliest, 1, "zw"))
        # rank: completes by deadline already filtered; then no changes, min total, earlier start, fewer payments, lowest opt id
        # our tuple already encodes (deadline_flag, changes_flag, total, start, npay, optid) but deadline_flag 0 for immediate, 1 for wait
        # spec order: 1 complete by deadline, 2 no changes, 3 min total, 4 earlier start, 5 fewer, 6 lowest id.
        # For wait vs immediate both complete by deadline; immediate should rank before wait? Spec lists "Prefer a plan that completes by deadline, avoids changes, minimizes cost, starts earlier..." wait starts later so immediate wins naturally via start date. Keep wait flag as tiebreak after.
        cands.sort(key=lambda x: x[0])
        if cands:
            best = cands[0]
            _, method, status, plan, e_out, chg, *_ = best
            earliest_out = e_out.isoformat() if isinstance(e_out, date) else ""
            # earliest_date_for_full_payment per spec: equals request_date for affordable_now; independent capacity measure
            if status == "affordable_now":
                earliest_out = req_date.isoformat()
            else:
                earliest_out = earliest.isoformat() if earliest else ""
            if method == "wait":
                # plan already earliest full
                pass
            # explanation
            if status == "affordable_now":
                expl = f"Pay {home} {fmt_amount(requested)} today. This leaves at least {home} {fmt_amount(minimum)} available over the next 90 days."
            elif method == "installments":
                expl = f"Use installments totalling {home} {fmt_amount(best[6])} starting {best[7].isoformat()}. This leaves at least {home} {fmt_amount(minimum)} available."
            elif method == "partial_payment":
                expl = f"Pay {home} {fmt_amount(safe)} today then {home} {fmt_amount(round(requested - safe, 2))} on {earliest.isoformat()}. This keeps the {home} {fmt_amount(minimum)} minimum protected."
            elif method == "wait":
                expl = f"Wait until {earliest.isoformat()}, then pay {home} {fmt_amount(requested)} in full. Paying sooner would put the {home} {fmt_amount(minimum)} minimum at risk."
            else:  # full with changes
                expl = f"Pay {home} {fmt_amount(requested)} in full with spending changes {chg}. This keeps the {home} {fmt_amount(minimum)} minimum protected."
        else:
            # not affordable
            method, status, plan = "not_recommended", "not_affordable", "none"
            chg = "none"
            earliest_out = ""
            expl = f"Do not make this payment by {des_date.isoformat() if des_date else req_date.isoformat()}. None of the available options keeps the {home} {fmt_amount(minimum)} minimum protected."
        out_rows.append({
            "request_id": rid,
            "amount_safe_to_pay": fmt_amount(max(0.0, min(safe, requested))),
            "affordability_status": status,
            "recommended_payment_method": method,
            "payment_plan": plan,
            "earliest_date_for_full_payment": earliest_out,
            "spending_changes_needed": chg,
            "decision_explanation": expl,
        })

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["request_id", "amount_safe_to_pay", "affordability_status", "recommended_payment_method", "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed", "decision_explanation"])
        w.writeheader()
        w.writerows(out_rows)
    print(f"Wrote {len(out_rows)} rows to {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
