import csv
import os
import re
from datetime import date, datetime, timedelta
from collections import defaultdict

def resolvepaths():
    here = os.path.dirname(os.path.abspath(__file__))
    cand1 = os.path.join(os.path.dirname(here), 'dataset')
    cand2 = os.path.join(here, 'dataset')
    if os.path.isdir(cand1):
        root = os.path.dirname(here)
    elif os.path.isdir(cand2):
        root = here
    else:
        root = os.path.dirname(here)
    return (root, os.path.join(root, 'dataset'), os.path.join(root, 'output.csv'))
REPO_ROOT, DATASET, OUTPUT_PATH = resolvepaths()
FORECAST_DAYS = 90
AMOUNT_RE = re.compile('(IDR|INR|ZAR|EUR|USD)\\s*([\\d][\\d,\\.]*)')
ISO_DATE_RE = re.compile('(\\d{4}-\\d{2}-\\d{2})')

def parse_date(s):
    s = (s or '').strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None

def parse_dt(s):
    s = (s or '').strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        try:
            return datetime.fromisoformat(s[:10])
        except Exception:
            return None

def parse_float(s):
    s = (s or '').strip().replace(',', '')
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None

def fmt_amount(x):
    x = round(float(x) + 1e-09, 2)
    if x < 0 and x > -0.005:
        x = 0.0
    s = f'{x:.2f}'
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s if s else '0'

def load_csv(path):
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))

def makefxstore(rows):
    by_pair = defaultdict(list)
    for r in rows:
        d = parse_date(r.get('rate_date'))
        f = (r.get('from_currency') or '').strip()
        t = (r.get('to_currency') or '').strip()
        v = parse_float(r.get('rate'))
        if d and f and t and v:
            by_pair[f, t].append((d, v))
    for k in by_pair:
        by_pair[k].sort()
    return by_pair

def fxrate(by_pair, frm, to, settle):
        if frm == to:
            return 1.0
        if settle is None:
            settle = date(2025, 1, 1)
        for d, v in by_pair.get((frm, to), []):
            if d == settle:
                return v
        for d, v in by_pair.get((frm, to), []):
            if d.year == settle.year and d.month == settle.month:
                return v
        lst = by_pair.get((frm, to), [])
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
        lst2 = by_pair.get((to, frm), [])
        for d, v in lst2:
            if d == settle and v:
                return 1.0 / v
        for d, v in lst2:
            if d.year == settle.year and d.month == settle.month and v:
                return 1.0 / v
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

        def direct(f, t):
            l = by_pair.get((f, t), [])
            b = None
            for d, v in l:
                if d <= settle:
                    b = v
                else:
                    break
            if b is not None:
                return b
            if l:
                return l[0][1]
            lr = by_pair.get((t, f), [])
            b = None
            for d, v in lr:
                if d <= settle:
                    b = v
                else:
                    break
            if b is None and lr:
                b = lr[0][1]
            return 1.0 / b if b else None
        for mid in ('USD', 'EUR'):
            if mid in (frm, to):
                continue
            r1 = direct(frm, mid)
            r2 = direct(mid, to)
            if r1 is not None and r2 is not None:
                return r1 * r2
        return 1.0

def fxconvert(by_pair, amount, frm, to, settle):
    if amount is None:
        return None
    if frm == to or frm == '' or to == '':
        return float(amount)
    return float(amount) * fxrate(by_pair, frm, to, settle)

def analyze_messages(msgs):
    info = defaultdict(lambda: {'salary_override': None, 'salary_amounts': [], 'employment_ended': None, 'rent_multiplier': 1.0, 'rent_mult_date': None, 'transfer_exclusion': False, 'salary_dates': [], 'force_exclude': set(), 'force_include': set()})
    for m in msgs:
        uid = m.get('user_id', '')
        text = m.get('message_text', '') or ''
        src = (m.get('source_type', '') or '').lower()
        sent = parse_dt(m.get('sent_at', ''))
        sent_d = sent.date() if isinstance(sent, datetime) else parse_date(m.get('sent_at', ''))
        low = text.lower()
        d = info[uid]
        rel = (m.get('related_event_id') or '').strip()
        if rel:
            if any((p in low for p in ['not reached', 'not been approved', 'has not reached', 'initiated but has not', 'still pending', 'can change until', "isn't withdrawable", 'not withdrawable', 'no cash proceeds', 'not sold', 'dispute', 'no reversal', 'do not credit', 'pay release charge', 'pay fee', 'fee to release'])):
                d['force_exclude'].add(rel)
            if any((p in low for p in ['still outstanding', 'another attempt', 'will retry', 'failed', 'another payment', 'overdue'])):
                d['force_include'].add(rel)
        if 'matching debit and credit' in low or 'transfer between your two accounts' in low or 'same account holder' in low:
            d['transfer_exclusion'] = True
        if 'increases monthly rent by 12%' in low or 'increased monthly rent' in low or 'rent by 12%' in low:
            d['rent_multiplier'] = 1.12
            d['rent_mult_date'] = sent_d
        if any((p in low for p in ['seasonal contract has ended', 'employment has ended', 'employment ended', 'no off-season income', 'no renewal has been confirmed'])):
            if sent_d and (d['employment_ended'] is None or sent_d < d['employment_ended']):
                d['employment_ended'] = sent_d
        if src == 'employer':
            is_pending_only = any((p in low for p in ['still pending', 'pending review', 'not been approved', 'not approved', 'awaiting', 'has not been approved', 'until the payout is closed', 'can change until']))
            amts = []
            for cur, num in AMOUNT_RE.findall(text):
                try:
                    v = float(num.replace(',', ''))
                    amts.append((cur, v))
                except Exception:
                    pass
            dates = ISO_DATE_RE.findall(text)
            if amts and ('salar' in low or 'pay' in low or 'gaji' in low or ('penggajian' in low)):
                chosen = max(amts, key=lambda x: x[1]) if len(amts) > 1 else amts[0]
                eff = None
                for ds in dates:
                    dd = parse_date(ds)
                    if dd:
                        eff = dd
                        break
                if eff is None:
                    eff = sent_d
                if not is_pending_only:
                    d['salary_amounts'].append((sent_d, chosen[1], chosen[0], text[:80]))
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

def detect_recurring(history):
    groups = defaultdict(list)
    for ed, amt, cat, direction in history:
        if ed is None or amt is None:
            continue
        groups[cat or '', direction or ''].append((ed, amt))
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
        tol = 3 if iv <= 14 else 5
        if iv == 91:
            tol = 8
        ok = sum((1 for x in intervals if abs(x - iv) <= tol))
        if ok < max(2, len(intervals) // 2):
            continue
        amts = [a for _, a in items[-6:]]
        out.append({'category': cat, 'direction': direction, 'interval': iv, 'amounts': amts, 'last_date': items[-1][0], 'count': len(items)})
    return out

def conservative_amount(rec, direction):
    amts = rec['amounts']
    if not amts:
        return 0.0
    if direction == 'debit':
        return max(amts)
    return min(amts)
SALARY_HINTS = ('salary', 'payroll', 'gaji', 'penggajian', 'pay ')

def is_salary_group(cat, items_sample_desc=''):
    c = (cat or '').lower()
    if 'salary' in c or 'payroll' in c or 'income' in c:
        return True
    return False

def build_flows(profile, user_events, msg_info, fx, request_date, home):
    flows = defaultdict(float)
    req = request_date
    end = req + timedelta(days=FORECAST_DAYS)
    exclude_pairs = set()
    if msg_info and msg_info.get('transfer_exclusion'):
        debits = [e for e in user_events if e.get('_dir') == 'debit' and e.get('_amt_home') is not None]
        credits = [e for e in user_events if e.get('_dir') == 'credit' and e.get('_amt_home') is not None]
        for de in debits:
            for ce in credits:
                try:
                    if abs(float(de['_amt_home']) - float(ce['_amt_home'])) < 0.01:
                        dd = de.get('_settle') or de.get('_event')
                        cd = ce.get('_settle') or ce.get('_event')
                        if dd and cd and (abs((dd - cd).days) <= 7):
                            ddesc = ((de.get('description') or '') + ' ' + (ce.get('description') or '')).lower()
                            if 'transfer' in ddesc or True:
                                exclude_pairs.add(de.get('event_id'))
                                exclude_pairs.add(ce.get('event_id'))
                except Exception:
                    pass
    history_for_recur = []
    scheduled = []
    salary_sched = []
    for e in user_events:
        eid = e.get('event_id')
        if eid in exclude_pairs:
            continue
        if msg_info and eid in msg_info.get('force_exclude', set()):
            if eid not in msg_info.get('force_include', set()):
                continue
        status = (e.get('status') or '').lower()
        if status in ('cancelled', 'failed', 'unrealized'):
            continue
        direction = (e.get('direction') or '').lower()
        if direction == 'non_cash':
            continue
        etype = (e.get('event_type') or '').lower()
        if etype == 'investment_valuation':
            continue
        amt_home = e.get('_amt_home')
        if amt_home is None:
            continue
        ev_d = e.get('_event')
        st_d = e.get('_settle') or ev_d
        cat = e.get('category') or ''
        desc = (e.get('description') or '') + ' ' + (e.get('event_type') or '')
        if status == 'pending' and direction == 'credit':
            continue
        if status == 'settled':
            if ev_d and ev_d < req:
                if direction == 'debit' and etype in ('expense', 'subscription', 'debt_payment', 'investment_purchase'):
                    history_for_recur.append((ev_d, amt_home, cat, direction))
                elif direction == 'credit' and ('salar' in desc.lower() or 'payroll' in desc.lower() or 'gaji' in desc.lower() or (etype == 'income')):
                    dl = desc.lower()
                    if any((k in dl for k in ['bonus', 'commission', 'refund', 'lottery', 'investment', 'prize', 'cashback', 'reversal'])):
                        pass
                    else:
                        history_for_recur.append((ev_d, amt_home, cat, direction))
            elif st_d and st_d >= req and (st_d <= end):
                sgn = -amt_home if direction == 'debit' else amt_home
                flows[st_d] += sgn
                scheduled.append((st_d, sgn, eid))
            continue
        if status == 'pending' and direction == 'debit':
            d = st_d
            if d is None:
                d = req
            if d < req:
                d = req
            if d <= end:
                flows[d] += -amt_home
                scheduled.append((d, -amt_home, eid))
            continue
        if status == 'scheduled':
            d = st_d or ev_d or req
            if d < req:
                d = req
            if d <= end:
                sgn = -amt_home if direction == 'debit' else amt_home
                if direction == 'credit':
                    dl = desc.lower()
                    if ('salar' in dl or 'payroll' in dl or 'gaji' in dl) or etype == 'income':
                        if any((k in dl for k in ['bonus', 'commission', 'lottery', 'prize'])) and 'salary' not in dl:
                            continue
                    elif any((k in dl for k in ['bonus', 'commission', 'lottery', 'prize', 'investment'])):
                        continue
                flows[d] += sgn
                scheduled.append((d, sgn, eid))
                if direction == 'credit' and ('salar' in desc.lower() or 'payroll' in desc.lower()):
                    salary_sched.append((d, amt_home, eid))
            continue
    recurs = detect_recurring(history_for_recur)
    emp_end = msg_info.get('employment_ended') if msg_info else None
    sal_override = None
    if msg_info and msg_info.get('salary_amounts'):
        lb = latest_salary_before(msg_info['salary_amounts'], req)
        if lb:
            _, amt_raw, cur_raw = lb
            sal_override = fxconvert(fx, amt_raw, cur_raw, home, req)
    rent_mult = msg_info.get('rent_multiplier', 1.0) if msg_info else 1.0
    rent_mult_date = msg_info.get('rent_mult_date') if msg_info else None
    sched_keys = [(d, s) for d, s, _ in scheduled]

    def near_scheduled(d, signed_amt, tol_days=4, tol_amt=0.05):
        for sd, ss in sched_keys:
            if abs((sd - d).days) <= tol_days and abs(ss - signed_amt) / max(1.0, abs(ss)) <= tol_amt + 1e-09:
                return True
        return False
    recur_flows = []
    for rec in recurs:
        cat = rec['category']
        direction = rec['direction']
        if direction == 'credit' and (not is_salary_group(cat)):
            continue
        if emp_end and direction == 'credit' and is_salary_group(cat):
            pass
        amt = conservative_amount(rec, direction)
        if direction == 'credit' and is_salary_group(cat) and (sal_override is not None):
            amt = sal_override
        if direction == 'debit' and rent_mult != 1.0 and ('rent' in cat.lower() or 'housing' in cat.lower()):
            pass
        last = rec['last_date']
        iv = rec['interval']
        nxt = last + timedelta(days=iv)
        while nxt < req:
            nxt += timedelta(days=iv)
        while nxt <= end:
            a = amt
            if direction == 'debit' and rent_mult != 1.0 and ('rent' in cat.lower() or 'housing' in cat.lower()):
                if rent_mult_date is None or nxt >= rent_mult_date:
                    a = amt * rent_mult
            if direction == 'credit' and is_salary_group(cat) and emp_end and (nxt > emp_end):
                nxt += timedelta(days=iv)
                continue
            sgn = -a if direction == 'debit' else a
            if not near_scheduled(nxt, sgn):
                flows[nxt] += sgn
                recur_flows.append((nxt, sgn, cat))
            nxt += timedelta(days=iv)
    return (flows, scheduled, recur_flows)

def cumulative_min_check(b0, flows, req, end, minimum, extra_payments):
    pay_by_day = defaultdict(float)
    for d, a in extra_payments:
        pay_by_day[d] += a
    bal = b0
    min_seen = bal
    d = req
    day = req
    while day <= end:
        net = flows.get(day, 0.0) - pay_by_day.get(day, 0.0)
        bal += net
        if bal < min_seen:
            min_seen = bal
        if bal < minimum - 1e-06:
            return (False, min_seen)
        day += timedelta(days=1)
    return (True, min_seen)

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
    min_surplus = min((v - minimum for v in cum.values())) if cum else b0 - minimum
    safe = max(0.0, min_surplus)
    safe = min(safe, requested)
    days = sorted(cum.keys())
    earliest = None
    suf_min = {}
    s = float('inf')
    for d in reversed(days):
        s = min(s, cum[d])
        suf_min[d] = s
    pre_min = float('inf')
    base_ok_prefix = True
    for d in days:
        if pre_min == float('inf'):
            pre_ok = True
        else:
            pre_ok = pre_min >= minimum - 1e-06
        if pre_ok and suf_min[d] - requested >= minimum - 1e-06:
            earliest = d
            break
        pre_min = cum[d] if pre_min == float('inf') else min(pre_min, cum[d])
    return (safe, earliest)

def main():
    profiles = {r['user_id']: r for r in load_csv(os.path.join(DATASET, 'financial_profiles.csv'))}
    events = load_csv(os.path.join(DATASET, 'financial_events.csv'))
    requests = load_csv(os.path.join(DATASET, 'requests.csv'))
    options = load_csv(os.path.join(DATASET, 'request_payment_options.csv'))
    messages = load_csv(os.path.join(DATASET, 'messages.csv'))
    fx = makefxstore(load_csv(os.path.join(DATASET, 'exchange_rates.csv')))
    msg_info = analyze_messages(messages)
    events_by_user = defaultdict(list)
    for e in events:
        uid = e.get('user_id')
        prof = profiles.get(uid)
        home = (prof.get('home_currency') if prof else '') or (e.get('currency') or '')
        amt = parse_float(e.get('amount'))
        cur = (e.get('currency') or '').strip() or home
        st = parse_date(e.get('settlement_date')) or parse_date(e.get('event_date'))
        ev = parse_date(e.get('event_date'))
        conv = None
        if amt is not None:
            conv = fxconvert(fx, amt, cur, home, st or ev or date(2025, 1, 1))
        e['_amt_home'] = conv
        e['_settle'] = parse_date(e.get('settlement_date'))
        e['_event'] = parse_date(e.get('event_date'))
        e['_dir'] = (e.get('direction') or '').lower()
        events_by_user[uid].append(e)
    opts_by_req = defaultdict(list)
    for o in options:
        opts_by_req[o.get('request_id')].append(o)
    for k in opts_by_req:
        opts_by_req[k].sort(key=lambda x: x.get('payment_option_id', ''))
    out_rows = []
    for rq in requests:
        rid = rq.get('request_id')
        uid = rq.get('user_id')
        prof = profiles.get(uid, {})
        home = (prof.get('home_currency') or '').strip()
        b0 = parse_float(prof.get('current_available_balance')) or 0.0
        minimum = parse_float(prof.get('minimum_balance_to_keep')) or 0.0
        req_date = parse_date(rq.get('request_date'))
        des_date = parse_date(rq.get('desired_completion_date'))
        requested = parse_float(rq.get('requested_amount')) or 0.0
        allows_partial = (rq.get('allows_partial_payment') or '').strip().lower() == 'true'
        consider = [(s or '').strip() for s in (prof.get('payment_methods_user_will_consider') or '').split('|') if (s or '').strip()]
        max_inst = parse_float(prof.get('max_installment_months'))
        end = req_date + timedelta(days=FORECAST_DAYS)
        uevents = events_by_user.get(uid, [])
        mi = msg_info.get(uid, {'rent_multiplier': 1.0, 'rent_mult_date': None, 'employment_ended': None, 'salary_amounts': [], 'transfer_exclusion': False})
        flows, scheduled, recur_flows = build_flows(prof, uevents, mi, fx, req_date, home)
        safe, earliest = compute_safe_and_earliest(b0, flows, req_date, end, minimum, requested)
        safe = round(safe + 1e-09, 2)
        protect = set((prof.get('expense_categories_to_protect') or '').split('|')) if prof.get('expense_categories_to_protect') else set()
        reduce_cats = set((prof.get('expense_categories_user_is_willing_to_reduce') or '').split('|')) if prof.get('expense_categories_user_is_willing_to_reduce') else set()
        stop_cats = set((prof.get('expense_categories_user_is_willing_to_stop') or '').split('|')) if prof.get('expense_categories_user_is_willing_to_stop') else set()
        reduce_cats = {c for c in reduce_cats if c}
        stop_cats = {c for c in stop_cats if c}
        flex_cands = []
        last_by_cat = {}
        for e in uevents:
            if (e.get('status') or '').lower() != 'settled':
                continue
            if e.get('_dir') != 'debit':
                continue
            if (e.get('flexibility') or '').lower() == 'fixed':
                continue
            cat = e.get('category') or ''
            if cat in protect:
                continue
            ed = e.get('_event')
            if ed and ed < req_date:
                if cat not in last_by_cat or ed > last_by_cat[cat].get('_event'):
                    last_by_cat[cat] = e
        for rec in detect_recurring([(e, 0, '', '')] if False else []):
            pass
        cnt90 = defaultdict(int)
        amt90 = {}
        for d, sgn, cat in recur_flows:
            if sgn < 0:
                cnt90[cat] += 1
                amt90[cat] = max(amt90.get(cat, 0.0), -sgn)
        hist_by_cat = defaultdict(list)
        for e in uevents:
            if (e.get('status') or '').lower() != 'settled':
                continue
            if e.get('_dir') != 'debit':
                continue
            if (e.get('flexibility') or '').lower() == 'fixed':
                continue
            cat = e.get('category') or ''
            if cat in protect or not cat:
                continue
            ed = e.get('_event')
            if ed and req_date - timedelta(days=180) <= ed < req_date and e.get('_amt_home'):
                hist_by_cat[cat].append((ed, float(e['_amt_home'])))
        for cat, items in hist_by_cat.items():
            if cat in cnt90 or len(items) < 2:
                continue
            items.sort()
            intervals = [(items[i + 1][0] - items[i][0]).days for i in range(len(items) - 1)]
            intervals = [iv for iv in intervals if iv > 0]
            avg_iv = sum(intervals) / len(intervals) if intervals else 30
            if avg_iv <= 10:
                n_est, step = (12, 7)
            elif avg_iv <= 20:
                n_est, step = (6, 14)
            elif avg_iv <= 45:
                n_est, step = (3, 30)
            else:
                n_est, step = (2, 45)
            orig = max((a for _, a in items[-4:]))
            d = items[-1][0] + timedelta(days=int(round(avg_iv)))
            added = 0
            while d < req_date:
                d += timedelta(days=int(round(avg_iv)) if avg_iv >= 1 else step)
            while d <= end and added < n_est:
                dup = False
                for sd, ss, _ in scheduled:
                    if abs((sd - d).days) <= 4 and abs(ss + orig) / max(1.0, abs(ss)) <= 0.05 + 1e-09:
                        dup = True
                        break
                if not dup:
                    flows[d] += -orig
                    recur_flows.append((d, -orig, cat))
                    cnt90[cat] += 1
                    amt90[cat] = max(amt90.get(cat, 0.0), orig)
                    added += 1
                d += timedelta(days=int(round(avg_iv)) if avg_iv >= 1 else step)
        for cat, rep in last_by_cat.items():
            if cat not in cnt90:
                continue
            flex = (rep.get('flexibility') or '').lower()
            n = cnt90[cat]
            orig = amt90.get(cat, rep.get('_amt_home') or 0.0)
            min_allowed = parse_float(rep.get('minimum_allowed_amount'))
            eid = rep.get('event_id')
            can_stop = ('stoppable' in flex or 'stop' in flex) or cat in stop_cats
            can_reduce = 'reducib' in flex or cat in reduce_cats
            if can_stop:
                flex_cands.append((orig * n, f'stop:{eid}', cat, eid, None, orig, n))
            if can_reduce:
                new_amt = min_allowed if min_allowed is not None and min_allowed < orig else round(orig * 0.5, 2)
                if new_amt < orig:
                    flex_cands.append(((orig - new_amt) * n, f'reduce_to:{eid}:{fmt_amount(new_amt)}', cat, eid, new_amt, orig, n))
        best_per_event = {}
        for sav, act, cat, eid, new_amt, orig, n in flex_cands:
            if eid not in best_per_event or sav > best_per_event[eid][0]:
                best_per_event[eid] = (sav, act, cat, new_amt, orig, n)
        ranked = sorted(best_per_event.values(), key=lambda x: -x[0])
        safe, earliest = compute_safe_and_earliest(b0, flows, req_date, end, minimum, requested)
        safe = round(safe + 1e-09, 2)

        def try_with_changes(change_acts):
            mod_flows = dict(flows)
            eid_to_cat = {}
            for sav, act, cat, new_amt, orig, n in ranked:
                m = re.search('(event_\\d+)', act)
                if m:
                    eid_to_cat[m.group(1)] = (cat, new_amt, orig)
            for ceid, cnew in change_acts:
                cat, _, orig = eid_to_cat.get(ceid, (None, None, None))
                if cat is None:
                    continue
                for d, sgn, c in recur_flows:
                    if c != cat:
                        continue
                    if cnew is None:
                        mod_flows[d] = mod_flows.get(d, 0.0) - sgn
                    else:
                        mod_flows[d] = mod_flows.get(d, 0.0) + (-cnew - sgn)
            return mod_flows
        full_needs_changes = False
        chosen_changes = []
        if not safe >= requested - 1e-06:
            for k in (1, 2, 3):
                if k > len(ranked):
                    break
                picks = []
                acts = []
                for sav, act, cat, new_amt, orig, n in ranked[:k]:
                    m = re.search('(event_\\d+)', act)
                    eid = m.group(1) if m else None
                    picks.append((eid, new_amt))
                    acts.append(act)
                mod = try_with_changes(picks)
                s2, e2 = compute_safe_and_earliest(b0, mod, req_date, end, minimum, requested)
                if s2 >= requested - 1e-06:
                    full_needs_changes = True
                    chosen_changes = acts
                    break
        cands = []
        full_opts = [o for o in opts_by_req.get(rid, []) if (o.get('payment_method') or '') == 'full_payment']
        full_opt = full_opts[0] if full_opts else None
        if 'full_payment' in consider and full_opt:
            if safe >= requested - 1e-06:
                fd = parse_date(full_opt.get('first_payment_date')) or req_date
                plan = f'{fd.isoformat()}:{fmt_amount(requested)}'
                cands.append(((0, 0, requested, fd.toordinal(), 1, full_opt.get('payment_option_id', '')), 'full_payment', 'affordable_now', plan, req_date, 'none', requested, fd, 1, full_opt.get('payment_option_id', '')))
            elif full_needs_changes:
                fd = parse_date(full_opt.get('first_payment_date')) or req_date
                plan = f'{fd.isoformat()}:{fmt_amount(requested)}'
                cands.append(((0, 1, requested, fd.toordinal(), 1, full_opt.get('payment_option_id', '')), 'full_payment', 'affordable_with_plan', plan, req_date, '|'.join(chosen_changes), requested, fd, 1, full_opt.get('payment_option_id', '')))
        if 'installments' in consider:
            for o in opts_by_req.get(rid, []):
                if (o.get('payment_method') or '') != 'installments':
                    continue
                n = int(parse_float(o.get('number_of_payments')) or 0)
                if n <= 0:
                    continue
                if max_inst is not None and n > max_inst + 1e-09:
                    continue
                first = parse_date(o.get('first_payment_date')) or req_date
                freq = parse_float(o.get('payment_frequency_days')) or 30
                per = parse_float(o.get('payment_amount')) or 0.0
                total = parse_float(o.get('total_payable_amount')) or per * n
                paydates = [first + timedelta(days=int(round(freq)) * i) for i in range(n)]
                if paydates[-1] > end + timedelta(days=365 * 2):
                    pass
                if des_date and paydates[-1] > des_date:
                    continue
                extra = [(d, per) for d in paydates]
                ok, _ = cumulative_min_check(b0, flows, req_date, end, minimum, extra)
                if ok:
                    plan = '|'.join((f'{d.isoformat()}:{fmt_amount(per)}' for d in paydates))
                    cands.append(((0, 0, total, first.toordinal(), n, o.get('payment_option_id', '')), 'installments', 'affordable_with_plan', plan, earliest, 'none', total, first, n, o.get('payment_option_id', '')))
        if allows_partial and 'partial_payment' in consider and (safe > 1e-06) and (safe < requested - 1e-06) and (earliest is not None) and des_date and (earliest <= des_date):
            rem = round(requested - safe, 2)
            extra = [(req_date, safe), (earliest, rem)]
            ok, _ = cumulative_min_check(b0, flows, req_date, end, minimum, extra)
            if ok:
                plan = f'{req_date.isoformat()}:{fmt_amount(safe)}|{earliest.isoformat()}:{fmt_amount(rem)}'
                cands.append(((0, 0, requested, req_date.toordinal(), 2, 'zz'), 'partial_payment', 'affordable_with_plan', plan, earliest, 'none', requested, req_date, 2, 'zz'))
        if 'full_payment' in consider and earliest is not None and (earliest > req_date):
            if des_date is None or earliest <= des_date + timedelta(days=0):
                ok, _ = cumulative_min_check(b0, flows, req_date, end, minimum, [(earliest, requested)])
                if ok:
                    plan = f'{earliest.isoformat()}:{fmt_amount(requested)}'
                    cands.append(((1, 0, requested, earliest.toordinal(), 1, 'zw'), 'wait', 'affordable_later', plan, earliest, 'none', requested, earliest, 1, 'zw'))
        cands.sort(key=lambda x: x[0])
        if cands:
            best = cands[0]
            _, method, status, plan, e_out, chg, *_ = best
            earliest_out = e_out.isoformat() if isinstance(e_out, date) else ''
            if status == 'affordable_now':
                earliest_out = req_date.isoformat()
            else:
                earliest_out = earliest.isoformat() if earliest else ''
            if method == 'wait':
                pass
            if status == 'affordable_now':
                expl = f'Pay {home} {fmt_amount(requested)} today. This leaves at least {home} {fmt_amount(minimum)} available over the next 90 days.'
            elif method == 'installments':
                expl = f'Use installments totalling {home} {fmt_amount(best[6])} starting {best[7].isoformat()}. This leaves at least {home} {fmt_amount(minimum)} available.'
            elif method == 'partial_payment':
                expl = f'Pay {home} {fmt_amount(safe)} today then {home} {fmt_amount(round(requested - safe, 2))} on {earliest.isoformat()}. This keeps the {home} {fmt_amount(minimum)} minimum protected.'
            elif method == 'wait':
                expl = f'Wait until {earliest.isoformat()}, then pay {home} {fmt_amount(requested)} in full. Paying sooner would put the {home} {fmt_amount(minimum)} minimum at risk.'
            else:
                expl = f'Pay {home} {fmt_amount(requested)} in full with spending changes {chg}. This keeps the {home} {fmt_amount(minimum)} minimum protected.'
        else:
            method, status, plan = ('not_recommended', 'not_affordable', 'none')
            chg = 'none'
            earliest_out = ''
            expl = f'Do not make this payment by {(des_date.isoformat() if des_date else req_date.isoformat())}. None of the available options keeps the {home} {fmt_amount(minimum)} minimum protected.'
        out_rows.append({'request_id': rid, 'amount_safe_to_pay': fmt_amount(max(0.0, min(safe, requested))), 'affordability_status': status, 'recommended_payment_method': method, 'payment_plan': plan, 'earliest_date_for_full_payment': earliest_out, 'spending_changes_needed': chg, 'decision_explanation': expl})
    with open(OUTPUT_PATH, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['request_id', 'amount_safe_to_pay', 'affordability_status', 'recommended_payment_method', 'payment_plan', 'earliest_date_for_full_payment', 'spending_changes_needed', 'decision_explanation'])
        w.writeheader()
        w.writerows(out_rows)
    print(f'Wrote {len(out_rows)} rows to {OUTPUT_PATH}')
if __name__ == '__main__':
    main()
