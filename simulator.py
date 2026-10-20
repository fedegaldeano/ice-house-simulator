"""
Ice House Toys - Christmas 2010 capacity simulator
==================================================

Runs on your computer:

    python -m pip install streamlit plotly pandas openpyxl
    python -m streamlit run simulator.py

The model, in order:
  1. SPACE   -> split the extra m² between packing, dispatch and stock.
  2. PEOPLE  -> the m² of each area set the maximum people at one time.
  3. PRODUCTIVITY -> orders per person per hour, per stage, plus the app.
  4. HOURS   -> normal hours, plus overtime (evenings) and Saturdays.
  5. DEMAND  -> 2010 orders from the case drivers, other growth, and
                moving part of November demand into October.
  6. FLOW    -> every week orders go Recording -> Picking -> Packing ->
                Dispatch. Each stage does what its capacity allows; the
                rest waits in a queue in front of it (backlog).
  7. COST    -> extra cost vs the 2009 set-up, with the case wages
                (Appendix 1), compared with the £4.08 net profit per order.

Every default comes from the case. Hover the (?) next to a field to see
what it means and where it comes from.
"""

import io

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ------------------------------------------------------------------ case data
STAGES = ["Recording", "Picking", "Packing", "Dispatch"]
STAGE_COLOR = {"Recording": "#2a78d6", "Picking": "#eb6834",
               "Packing": "#1baf7a", "Dispatch": "#eda100"}
C_ORDERS, C_CAPACITY, C_DONE = "#2a78d6", "#eb6834", "#1baf7a"
C_CRITICAL, C_MUTED = "#d03b3b", "#8a8984"

CAL = ([(2010, "Jul", 4)]
       + [(2010, m, w) for m in ["Aug", "Sep", "Oct", "Nov", "Dec"]
          for w in range(1, 5)]
       + [(2011, "Jan", w) for w in range(1, 5)])
ORDERS_2009 = {("Oct", 2): 300, ("Oct", 3): 800, ("Oct", 4): 1800,
               ("Nov", 1): 2900, ("Nov", 2): 3300, ("Nov", 3): 3700,
               ("Nov", 4): 3500, ("Dec", 1): 2800, ("Dec", 2): 2200,
               ("Dec", 3): 1300, ("Dec", 4): 75}
CLOSED = {("Dec", 4)}
FIRST_DELIVERY = ("Oct", 1)
OFFICE_M2 = 600          # second-floor office, one 600 m² working area
FIRST_SHARE_2009 = 0.75   # the 2009 first delivery (75%) filled the stockrooms
LAST_WEEK_BEFORE_XMAS = ("Dec", 3)

BASE_PEOPLE = {"Recording": 7, "Picking": 3, "Packing": 16, "Dispatch": 9}
BASE_RATE = {"Recording": 22.0, "Picking": 26.7, "Packing": 4.3,
             "Dispatch": 9.5}
# Appendix 1: wages per average parcel, 2009 (£)
CASE_COST_PER_ORDER = {"Recording": 0.35, "Picking": 0.12, "Packing": 0.77,
                       "Dispatch": 0.35}
NET_PROFIT_PER_ORDER = 4.08
# Appendix 1: 2009 cost analysis for one average parcel (one order = one parcel)
P09 = {"sales": 42.00, "stock": 23.00, "fixed": 10.08, "catalogue": 3.25}
ORDERS_09, MAILINGS_09 = 22600, 160000
FIXED_TOTAL = P09["fixed"] * ORDERS_09          # £227,808, kept constant
CAT_PER_MAILING = P09["catalogue"] * ORDERS_09 / MAILINGS_09   # ≈ £0.46
APP_ORDER_VALUE = 60.0                          # case: app orders ≈ £60
FREQ_DELIVERY_LOSS = 0.08                       # 8% of gross profit


def calendar_frame(orders_2009=None, open_flags=None):
    rows = []
    for i, (y, m, w) in enumerate(CAL):
        o = (orders_2009[i] if orders_2009 is not None
             else ORDERS_2009.get((m, w), 0))
        op = (open_flags[i] if open_flags is not None
              else (0 if (m, w) in CLOSED else 1))
        rows.append({"year": y, "month": m, "week": w, "label": f"{m} w{w}",
                     "orders_2009": float(o or 0), "open": int(op)})
    return pd.DataFrame(rows)


def read_sheet(file):
    """Optional: weekly 2009 orders and open weeks from the 'Plan 2010' tab."""
    data = file.getvalue()
    if file.name.lower().endswith(".csv"):
        full = pd.read_csv(io.BytesIO(data), header=None)
    else:
        full = pd.read_excel(io.BytesIO(data), sheet_name="Plan 2010",
                             header=None)
    full = full.reindex(columns=range(26))
    hdr = full.index[full[0].astype(str).str.strip() == "Year"]
    if len(hdr) == 0:
        raise ValueError("weekly table not found (no 'Year' header)")
    start = full.index.get_loc(hdr[0]) + 1
    block = full.iloc[start:start + len(CAL)]
    num = lambda s: pd.to_numeric(s, errors="coerce").fillna(0)
    return calendar_frame(num(block[3]).tolist(),
                          num(block[10]).astype(int).tolist())


# ------------------------------------------------------------------ model
@st.cache_data(show_spinner=False, max_entries=64)
def simulate(cal, p):
    df = cal.copy()
    n = len(df)
    labels = df["label"].tolist()
    months = df["month"].tolist()
    weeks = df["week"].tolist()
    is_open = df["open"].tolist()

    # 1. Demand
    resp = p["orders_2009"] / p["mailings_2009"]
    total = (p["orders_2009"] + p["new_names"] * resp
             + p["adv_spend"] / 1000 * p["orders_per_1000"]
             + (p["orders_2009"] * p["app_share"] if p["app_new"] else 0))
    total *= 1 + p["growth"]
    base = df["orders_2009"] / df["orders_2009"].sum() * total
    orders = base.tolist()
    nov = [i for i, m in enumerate(months) if m == "Nov"]
    octo = [i for i, m in enumerate(months) if m == "Oct" and base[i] > 0]
    moved = sum(base[i] for i in nov) * p["shift"]
    for i in nov:
        orders[i] -= base[i] * p["shift"]
    oct_sum = sum(base[i] for i in octo)
    for i in octo:
        orders[i] += moved * base[i] / oct_sum

    # 2. Capacity per hour of each stage
    app = p["app_share"]
    rate = dict(p["rate"])
    rate["Recording"] = 1 / (app / p["rate_app"]
                             + (1 - app) / p["rate"]["Recording"])
    per_hour = {s: rate[s] * p["people"][s] for s in STAGES}

    # 3. Hours per week (overtime and Saturdays only in the chosen weeks)
    d = p["days"]
    shift_h = {s: p["hours_rec"] if s == "Recording" else p["hours_wh"]
               for s in STAGES}
    ot_w = [p["ot_hours"] if lb in p["ot_weeks"] else 0.0 for lb in labels]
    sat_w = [1 if lb in p["sat_weeks"] else 0 for lb in labels]
    on_ot = {s: s in p["ot_stages"] for s in STAGES}
    ot = {s: [ot_w[i] if on_ot[s] else 0.0 for i in range(n)] for s in STAGES}
    sat = {s: [sat_w[i] if on_ot[s] else 0 for i in range(n)] for s in STAGES}
    hours = {s: [(d * (shift_h[s] + ot[s][i]) + sat[s][i] * shift_h[s])
                 * is_open[i] for i in range(n)] for s in STAGES}
    cap = {s: [per_hour[s] * h for h in hours[s]] for s in STAGES}
    capacity = [min(cap[s][i] for s in STAGES) for i in range(n)]

    # 4. Stock: what to buy and how much fits
    units_2009 = p["purchase_2009"] / p["unit_value"]
    margin = units_2009 / p["orders_2009"] - 1
    m2_unit = p["stock_m2"] / (FIRST_SHARE_2009 * units_2009)
    area = {"Packing": p["packing_m2"] + p["extra_pack"],
            "Dispatch": p["dispatch_m2"] + p["extra_disp"],
            "Stock": p["stock_m2"] + p["extra_area"] - p["extra_pack"]
                     - p["extra_disp"]
                     + (OFFICE_M2 if p["office_to_stock"] else 0)}
    stock_cap = max(area["Stock"], 0) / m2_unit      # orders' worth that fit
    bought = total * (1 + margin)
    first_i = next(i for i in range(n) if (months[i], weeks[i]) == FIRST_DELIVERY)
    entry = [0.0] * n
    deliveries = []
    # re-orders arrive every `interval` weeks counted from the first delivery
    follow_weeks = [first_i + j * p["interval"]
                    for j in range(1, p["n_follow"] + 1)]
    to_deliver = bought          # still to arrive
    left = 1 + p["n_follow"]     # deliveries left, first one included

    # 5. Weekly flow through the four stages
    q = {s: 0.0 for s in STAGES}
    stock = 0.0
    out = {k: [] for k in ["dispatched", "backlog", "backlog_days", "stock",
                           "stock_peak"] + [f"q_{s}" for s in STAGES]}
    done_total = {s: 0.0 for s in STAGES}
    for i in range(n):
        # deliveries arriving this week, capped by free stockroom space
        arriving = (i == first_i) or (i in follow_weeks)
        if arriving and left > 0:
            plan = (bought * p["first_share"] if i == first_i
                    else to_deliver / left)
            if p["n_follow"] == 0:
                plan = to_deliver
            got = max(0.0, min(plan, stock_cap - stock))
            deliveries.append({"Delivery": len(deliveries) + 1,
                               "Arrives": labels[i], "Planned": plan,
                               "Delivered": got, "Cut by space": plan - got})
            entry[i] = got
            to_deliver -= got
            left -= 1
            stock += got
        q["Recording"] += orders[i]
        out["stock_peak"].append(stock)
        for j, s in enumerate(STAGES):
            limit = cap[s][i]
            if s == "Picking":
                limit = min(limit, stock)       # cannot pick missing stock
            done = min(q[s], limit)
            q[s] -= done
            done_total[s] += done
            if s == "Picking":
                stock -= done
            if j + 1 < len(STAGES):
                q[STAGES[j + 1]] += done
        backlog = sum(q.values())
        daily = capacity[i] / d if capacity[i] > 0 else None
        out["dispatched"].append(done)
        out["backlog"].append(backlog)
        out["backlog_days"].append(backlog / daily if daily else None)
        out["stock"].append(stock)
        for s in STAGES:
            out[f"q_{s}"].append(q[s])

    df["orders"] = orders
    df["capacity"] = capacity
    df["entry"] = entry
    for k_, v_ in out.items():
        df[k_] = v_
    for s in STAGES:
        df[f"cap_{s}"] = cap[s]
    bott_w = [min(STAGES, key=lambda s: cap[s][i]) for i in range(n)]
    hb = [hours[bott_w[i]][i] for i in range(n)]
    df["bott_stage"] = bott_w
    df["hours_bott"] = hb
    df["minutes_bott"] = [h * 60 for h in hb]
    df["days_w"] = [(d + sat_w[i]) * is_open[i] for i in range(n)]
    df["ot_day"] = ot_w
    df["sat"] = sat_w
    df["takt"] = [hb[i] * 60 / orders[i] if orders[i] > 0 and is_open[i]
                  else None for i in range(n)]
    df["cycle"] = [hb[i] * 60 / capacity[i] if capacity[i] > 0 else None
                   for i in range(n)]
    df["min_stock"] = [sum(orders[i + 1:i + 3]) for i in range(n)]

    df["stock_cap"] = stock_cap
    late = [w_ for w_ in follow_weeks if w_ >= n]

    # 7. Cost vs the 2009 set-up (case wages)
    season = [orders[i] > 0 or out["backlog"][i] > 0 for i in range(n)]
    ot_cost = staff_cost = 0.0
    stage_extra = {}
    for s in STAGES:
        w = p["wage"][s]
        ot_h = sum((ot[s][i] * d + sat[s][i] * shift_h[s]) * is_open[i]
                   for i in range(n))
        ot_s = ot_h * p["people"][s] * w * (1 + p["ot_premium"])
        reg_h = sum(d * shift_h[s] * is_open[i] * season[i] for i in range(n))
        st_s = (p["people"][s] - BASE_PEOPLE[s]) * reg_h * w
        ot_cost += ot_s
        staff_cost += st_s
        stage_extra[s] = ot_s + st_s

    xmas = [i for i in range(n)
            if (months[i], weeks[i]) == LAST_WEEK_BEFORE_XMAS]
    normal_cap = {s: per_hour[s] * d * shift_h[s] for s in STAGES}
    bott = min(normal_cap, key=normal_cap.get)
    in_season = [i for i in range(n) if season[i]]
    util = {s: (done_total[s] / sum(cap[s][i] for i in in_season)
                if in_season and sum(cap[s][i] for i in in_season) else 0)
            for s in STAGES}
    extra_cost = ot_cost + staff_cost
    bd = [x for x in out["backlog_days"] if x is not None]
    k = {
        "total": total, "peak": max(orders), "bottleneck": bott,
        "cycle": 60 / per_hour[bott], "rate": rate, "per_hour": per_hour,
        "normal_cap": normal_cap, "util": util, "shift_h": shift_h,
        "max_backlog": max(out["backlog"]),
        "max_backlog_days": max(bd) if bd else 0,
        "weeks_over": sum(1 for x in bd if x > p["policy"]),
        "weeks_short": int((df["takt"] < df["cycle"]).sum()),
        "not_by_xmas": out["backlog"][xmas[0]] if xmas else 0,
        "stockout_weeks": sum(1 for i in range(n) if out["stock"][i] <= 0.5
                              and out["q_Picking"][i] > 0.5),
        "area": area,
        "stock_m2_peak": max(out["stock_peak"]) * m2_unit,
        "m2_unit": m2_unit, "margin": margin, "units_2009": units_2009,
        "bought": bought, "not_delivered": to_deliver,
        "cut": sum(d_["Cut by space"] for d_ in deliveries),
        "deliveries": deliveries, "late_reorders": len(late),
        "below_min": sum(1 for i in range(n) if (orders[i] > 0)
                         and out["stock"][i] < df["min_stock"][i] - 0.5),
        "ot_cost": ot_cost, "staff_cost": staff_cost,
        "extra_cost": extra_cost, "cost_per_order": extra_cost / total,
        "stage_extra": stage_extra,
    }
    # display rounding: counts as integers, times and days with 2 decimals
    for c_ in ["orders", "capacity", "entry", "dispatched", "backlog", "stock",
               "stock_peak", "min_stock", "stock_cap", "minutes_bott"] + \
            [f"q_{s}" for s in STAGES] + [f"cap_{s}" for s in STAGES]:
        df[c_] = df[c_].round(0)
    for c_ in ["takt", "cycle", "backlog_days", "hours_bott"]:
        df[c_] = df[c_].round(2)
    return df, k


def pnl(p, k):
    """Appendix 1 per parcel: 2009 vs the new scenario (one order = one parcel)."""
    n_ = k["total"]
    app = p["app_share"]
    sales = (1 - app) * P09["sales"] + app * APP_ORDER_VALUE
    stock = sales * P09["stock"] / P09["sales"]
    gross = sales - stock
    freq = FREQ_DELIVERY_LOSS * gross if p["n_follow"] > 1 else 0.0
    mailings = MAILINGS_09 + p["new_names"]
    cat = (mailings * CAT_PER_MAILING + p["adv_spend"]) / n_
    fixed = FIXED_TOTAL / n_
    wages = {s: CASE_COST_PER_ORDER[s] + k["stage_extra"][s] / n_
             for s in STAGES}
    net = gross - freq - fixed - cat - sum(wages.values())
    rows = [("Sales", P09["sales"], sales),
            ("Cost of stock, packaging materials, carriage", -P09["stock"],
             -stock),
            ("GROSS PROFIT", P09["sales"] - P09["stock"], gross),
            ("Loss of supplier discounts (frequent deliveries)", 0.0, -freq),
            ("Share of fixed costs", -P09["fixed"], -fixed),
            ("Catalogue and web pages production", -P09["catalogue"], -cat),
            ("Recording operators wages", -CASE_COST_PER_ORDER["Recording"],
             -wages["Recording"]),
            ("Stock collectors wages", -CASE_COST_PER_ORDER["Picking"],
             -wages["Picking"]),
            ("Packer wages", -CASE_COST_PER_ORDER["Packing"],
             -wages["Packing"]),
            ("Dispatcher wages", -CASE_COST_PER_ORDER["Dispatch"],
             -wages["Dispatch"]),
            ("NET PROFIT", NET_PROFIT_PER_ORDER, net)]
    t = pd.DataFrame(rows, columns=["Line", "2009 (£/parcel)",
                                    "New scenario (£/parcel)"])
    t["Change (£/parcel)"] = t["New scenario (£/parcel)"] - t["2009 (£/parcel)"]
    t["2009 total (£)"] = t["2009 (£/parcel)"] * ORDERS_09
    t["New scenario total (£)"] = t["New scenario (£/parcel)"] * n_
    num = t.columns[1:]
    t[num] = t[num].round(2) + 0.0          # + 0.0 turns -0.00 into 0.00
    return t, {"net": net, "sales": sales, "freq": freq}


# ------------------------------------------------------------------ widgets
def kk(name):
    """Widget key with a version; the reset button bumps the version."""
    return f"{name}__v{st.session_state.get('ver', 0)}"


def int_slider(label, lo, hi, key, default, step=1, help=None):
    """Slider whose maximum can change; keeps its value inside the limits."""
    hi = max(int(hi), int(lo))
    key = kk(key)
    if key not in st.session_state:
        st.session_state[key] = int(default)
    st.session_state[key] = int(min(max(st.session_state[key], lo), hi))
    if hi - lo < step:
        st.number_input(label, value=hi, disabled=True, key=key + "_locked",
                        help=help)
        return hi
    return st.slider(label, int(lo), hi, step=step, key=key, help=help)


def money(x, dec=0):
    sign = "−" if x < -0.5 * 10 ** -dec else ""
    return f"{sign}£{abs(x):,.{dec}f}"


# ------------------------------------------------------------------ page
st.set_page_config(page_title="Ice House Toys simulator", layout="wide")
st.title("Ice House Toys: Christmas 2010 simulator")
st.caption("Space → people → productivity → hours → results. "
           "Change the levers on the left; everything recalculates.")

p = {}
with st.sidebar:
    if st.button("↺ Reset to 2009 set-up", width="stretch", key="reset",
                 help="Puts every lever back to how the warehouse ran in "
                      "2009, with the demand expected for 2010. Saved "
                      "scenarios are kept."):
        st.session_state["ver"] = st.session_state.get("ver", 0) + 1
        st.rerun()

    # 1. SPACE --------------------------------------------------------------
    st.header("1. Space")
    p["extra_area"] = st.number_input(
        "Extra area from September (m²)", 0, 3000, 600, 50,
        key=kk("extra_area"),
        help="Ground-floor area moved to e-commerce from September 2010. "
             "Case value: 600 m².")
    p["extra_pack"] = int_slider(
        "Extra area for packing (m²)", 0, p["extra_area"], "extra_pack", 0,
        step=10,
        help="Part of the extra area given to packing. More m² allows more "
             "packers at the same time. What you do not give to packing or "
             "dispatch is used for stock.")
    p["extra_disp"] = int_slider(
        "Extra area for dispatch (m²)", 0, p["extra_area"] - p["extra_pack"],
        "extra_disp", 0, step=10,
        help="Part of the extra area given to dispatch. More m² allows more "
             "dispatchers at the same time.")
    st.caption(f"Extra area left for stock: "
               f"**{p['extra_area'] - p['extra_pack'] - p['extra_disp']:,} m²**")
    p["remote_rec"] = st.checkbox(
        "Remote recording staff (and remote mailroom)", False,
        key=kk("remote_rec"),
        help="Recording operators work from home and the post is handled "
             "off-site. The office desks no longer limit how many operators "
             "you can have, and the office becomes free.")
    p["office_to_stock"] = (st.checkbox(
        f"Use the freed office as stockroom (+{OFFICE_M2} m²)", True,
        key=kk("office_to_stock"),
        help="The office is one 600 m² working area on the second floor, "
             "next to a stockroom. With remote recording it can hold stock.")
        if p["remote_rec"] else False)
    with st.expander("Space standards"):
        p["packing_m2"] = st.number_input(
            "Packing area today (m²)", 0, 2000, 330, 10, key=kk("packing_m2"),
            help="Space for packing today: enough for 16 packers.")
        p["dispatch_m2"] = st.number_input(
            "Dispatch area today (m²)", 0, 2000, 270, 10,
            key=kk("dispatch_m2"),
            help="Rest of the 600 m² parcels room (600 − 330).")
        p["stock_m2"] = st.number_input(
            "Stockroom area today (m²)", 0, 10000, 1800, 100,
            key=kk("stock_m2"), help="Three stockrooms of 600 m² each.")
        p["m2_packer"] = st.number_input(
            "Space per packer (m²)", 5.0, 60.0, round(330 / 16, 1), 0.5,
            key=kk("m2_packer"), help="330 m² ÷ 16 packers ≈ 20.6 m².")
        p["m2_dispatcher"] = st.number_input(
            "Space per dispatcher (m²)", 5.0, 80.0, 30.0, 1.0,
            key=kk("m2_dispatcher"),
            help="The case says each dispatcher needs at least 30 m².")
        desks = st.number_input(
            "Recording desks", 1, 40, 7, key=kk("desks"),
            help="The office has room for up to seven operators.")
        max_pickers = st.number_input(
            "Maximum pickers", 1, 40, 12, key=kk("max_pickers"),
            help="Picking happens in the stockroom aisles, so the case gives "
                 "no space limit. This is only the top of the slider.")

    max_people = {
        "Recording": 40 if p["remote_rec"] else int(desks),
        "Picking": int(max_pickers),
        "Packing": int((p["packing_m2"] + p["extra_pack"]) // p["m2_packer"]),
        "Dispatch": int((p["dispatch_m2"] + p["extra_disp"])
                        // p["m2_dispatcher"])}

    # 2. PEOPLE -------------------------------------------------------------
    st.header("2. People at one time")
    people_help = {
        "Recording": "Agency operators entering orders. 2009: 7. "
                     "Maximum = number of desks.",
        "Picking": "Stock collectors who pick each batch of 20 orders. "
                   "2009: 3.",
        "Packing": "Packers who check and box each order. 2009: 16. "
                   "Maximum = packing area ÷ space per packer.",
        "Dispatch": "People who label and confirm each parcel. 2009: 9. "
                    "Maximum = dispatch area ÷ space per dispatcher."}
    why = {"Recording": ("remote, no desk limit" if p["remote_rec"]
                         else f"{desks} desks"),
           "Picking": "no space limit",
           "Packing": f"{p['packing_m2'] + p['extra_pack']:,} m² ÷ "
                      f"{p['m2_packer']} m²",
           "Dispatch": f"{p['dispatch_m2'] + p['extra_disp']:,} m² ÷ "
                       f"{p['m2_dispatcher']:.0f} m²"}
    p["people"] = {}
    for s in STAGES:
        p["people"][s] = int_slider(s, 1, max_people[s], f"people_{s}",
                                    BASE_PEOPLE[s], help=people_help[s])
        st.caption(f"Maximum {max_people[s]} ({why[s]})")

    # 3. PRODUCTIVITY -------------------------------------------------------
    st.header("3. Productivity")
    st.caption("Orders per person per hour. Raise one to test a process "
               "improvement.")
    p["rate"] = {
        "Recording": st.number_input(
            "Recording, post and web (orders/hour)", 1.0, 200.0, 22.0, 1.0,
            key=kk("rate_rec"),
            help="Paper and web orders are both typed in by hand at 22 per "
                 "hour, because the web shop is not linked to the system."),
        "Picking": st.number_input(
            "Picking (orders/hour)", 1.0, 200.0, 26.7, 0.5,
            key=kk("rate_pick"), help="20 orders every 45 minutes."),
        "Packing": st.number_input(
            "Packing (orders/hour)", 0.5, 50.0, 4.3, 0.1,
            key=kk("rate_pack"), help="Parcels per packer per hour."),
        "Dispatch": st.number_input(
            "Dispatch (orders/hour)", 1.0, 100.0, 9.5, 0.5,
            key=kk("rate_disp"), help="Orders per dispatcher per hour."),
    }
    p["app_share"] = st.slider(
        "Share of orders placed through the app", 0, 100, 5, format="%d%%",
        key=kk("app_share"),
        help="App orders do not need typing: the operator only checks them. "
             "A higher share raises the average recording speed. Case: about "
             "5% in 2010, doubling every year.") / 100
    p["rate_app"] = st.number_input(
        "Recording, app orders (orders/hour)", 1.0, 300.0, 60.0, 5.0,
        key=kk("rate_app"),
        help="Checking an app order takes about one minute.")
    blended = 1 / (p["app_share"] / p["rate_app"]
                   + (1 - p["app_share"]) / p["rate"]["Recording"])
    st.caption(f"Average recording speed: **{blended:.1f} orders/hour**")

    # 4. HOURS --------------------------------------------------------------
    st.header("4. Hours")
    with st.expander("Normal working time"):
        p["days"] = st.number_input(
            "Working days per week", 1, 6, 5, key=kk("days"),
            help="Monday to Friday.")
        p["hours_wh"] = st.number_input(
            "Warehouse hours per day", 1.0, 12.0, 9.0, 0.5,
            key=kk("hours_wh"),
            help="9 a.m. to 6 p.m., covered by part-timers on 4-hour morning "
                 "and 5-hour afternoon shifts.")
        p["hours_rec"] = st.number_input(
            "Recording hours per day", 1.0, 12.0, 7.0, 0.5,
            key=kk("hours_rec"), help="Each operator works a 7-hour day.")
    p["ot_hours"] = st.slider(
        "Overtime (extra hours per day)", 0.0, 4.0, 0.0, 0.5,
        key=kk("ot_hours"),
        help="Evening work after 6 p.m. in the weeks you pick below. The case "
             "treats evening and weekend work as overtime.")
    p["ot_stages"] = st.multiselect(
        "Stages working overtime", STAGES, default=STAGES,
        key=kk("ot_stages"),
        help="Only these stages work the overtime and Saturdays you set. "
             "Usually only the bottleneck needs it.")
    cal_slot = st.empty()

    # 5. DEMAND -------------------------------------------------------------
    st.header("5. Demand")
    p["shift"] = st.slider(
        "November demand moved to October", 0, 50, 0, format="%d%%",
        key=kk("shift"),
        help="Demand management: mail the catalogue earlier or offer "
             "early-bird deals so part of November's orders arrive in "
             "October, when there is spare capacity.") / 100
    p["growth"] = st.slider(
        "Other growth", -30, 150, 0, format="%d%%", key=kk("growth"),
        help="Any change in total demand on top of the 2010 drivers. Example: "
             "the case expects 15% a year from 2011, so +15% for 2011, +32% "
             "for 2012 (1.15 × 1.15), +52% for 2013.") / 100
    with st.expander("Demand drivers"):
        p["orders_2009"] = st.number_input(
            "Christmas orders in 2009", 0, 200000, 22600, 100,
            key=kk("orders_2009"),
            help="Orders from the 2009 Christmas catalogue.")
        p["mailings_2009"] = st.number_input(
            "Catalogues mailed in 2009", 1, 2000000, 160000, 1000,
            key=kk("mailings_2009"),
            help="Gives the response rate: 22,600 ÷ 160,000 ≈ 14.1%.")
        p["new_names"] = st.number_input(
            "New names from shared database", 0, 500000, 30000, 1000,
            key=kk("new_names"),
            help="Extra customers from the database swap. They order at the "
                 "2009 response rate.")
        p["adv_spend"] = st.number_input(
            "Extra advertising (£)", 0, 500000, 18000, 1000,
            key=kk("adv_spend"), help="Extra spend on ad-words in 2010.")
        p["orders_per_1000"] = st.number_input(
            "Orders per £1,000 of advertising", 0, 2000, 190, 10,
            key=kk("orders_per_1000"),
            help="Each £1,000 of ad-words has brought 190 extra orders.")
        p["app_new"] = st.checkbox(
            "App brings new customers", False, key=kk("app_new"),
            help="Off: the app is for existing customers, so it only changes "
                 "the channel. On: app orders are added on top.")
        up = st.file_uploader(
            "Weekly pattern from your spreadsheet", type=["xlsx", "csv"],
            key=kk("upload"),
            help="Optional. Reads the weekly 2009 orders from the 'Plan 2010' "
                 "tab of the downloaded Excel file.")

    # 6. STOCK REPLENISHMENT ----------------------------------------------
    st.header("6. Stock replenishment")
    p["purchase_2009"] = st.number_input(
        "Stock bought in 2009 (£, resale value)", 100000, 5000000, 1100000,
        50000, key=kk("purchase_2009"),
        help="Total stock ordered for Christmas 2009, valued at resale "
             "price: £1.1 million.")
    p["unit_value"] = st.number_input(
        "Value of one order of stock (£)", 10.0, 200.0, 42.0, 1.0,
        key=kk("unit_value"),
        help="Turns £ of stock into orders' worth. £42 = actual average "
             "order in 2009; £45 = the value Robin used to plan the "
             "purchase. Sets the safety margin and the m² per order.")
    _units = p["purchase_2009"] / p["unit_value"]
    st.caption(f"= **{_units:,.0f} orders' worth**: "
               f"{_units / p['orders_2009'] - 1:+.1%} over the "
               f"{p['orders_2009']:,} orders of 2009 (safety margin). The "
               f"first 75% filled the {p['stock_m2']:,} m² stockrooms → "
               f"**{p['stock_m2'] / (0.75 * _units):.4f} m² per order**.")
    p["first_share"] = st.slider(
        "First delivery (October week 1)", 0, 100, 75, format="%d%%",
        key=kk("first_share"),
        help="Share of the season's stock in the first delivery. It can "
             "never exceed the free stockroom space; what does not fit "
             "moves to the next deliveries.") / 100
    p["n_follow"] = st.slider(
        "Number of re-orders", 0, 4, 1, key=kk("n_follow"),
        help="Further deliveries after the first one. 2009: one re-order. "
             "The rest of the stock is split equally between them, each "
             "capped by the free space when it arrives. The case warns that "
             "smaller, more frequent deliveries lose supplier discounts "
             "(about 8% of gross margin).")
    p["interval"] = st.slider(
        "Weeks between deliveries", 1, 12, 7, key=kk("interval"),
        help="Counted from the first delivery in October week 1. Each "
             "re-order arrives this many weeks after the previous delivery. "
             "7 weeks = November week 4, as in 2009.")
    _arr = [CAL[9 + j * p["interval"]][1] + " w" + str(CAL[9 + j * p["interval"]][2])
            if 9 + j * p["interval"] < len(CAL) else "after the season"
            for j in range(1, p["n_follow"] + 1)]
    st.caption("Deliveries: Oct w1" + "".join(f", {a_}" for a_ in _arr))

    # 7. SETTINGS -----------------------------------------------------------
    st.header("7. Policy and cost")
    with st.expander("Settings"):
        p["policy"] = st.number_input(
            "Dispatch promise (days)", 1, 30, 3, key=kk("policy"),
            help="Policy: dispatch every order within three days.")
        cpo = {s: st.number_input(
            f"Wages per order, {s.lower()} (£)", 0.0, 10.0,
            CASE_COST_PER_ORDER[s], 0.01, format="%.2f", key=kk(f"cpo_{s}"),
            help="Appendix 1, cost per average parcel. ÷ productivity gives "
                 "the hourly wage.") for s in STAGES}
        p["wage"] = {s: cpo[s] * BASE_RATE[s] for s in STAGES}
        st.caption("Hourly wage: " + ", ".join(
            f"{s.lower()} £{p['wage'][s]:.2f}" for s in STAGES))
        p["ot_premium"] = st.slider(
            "Overtime premium", 0, 100, 50, format="%d%%",
            key=kk("ot_premium"),
            help="Extra paid per overtime or Saturday hour. Not given in the "
                 "case; 50% is the usual time and a half.") / 100

cal = calendar_frame()
if up is not None:
    try:
        cal = read_sheet(up)
    except Exception as e:  # noqa: BLE001
        st.sidebar.error(f"Could not read the file, using case data: {e}")

season_labels = cal.loc[cal["orders_2009"].gt(0) & cal["open"].eq(1),
                        "label"].tolist()
with cal_slot.container():
    p["ot_weeks"] = st.multiselect(
        "Weeks with overtime", season_labels, key=kk("ot_weeks"),
        help="Pick only the weeks that need it, usually the November peak.")
    p["sat_weeks"] = st.multiselect(
        "Weeks open on Saturday", season_labels, key=kk("sat_weeks"),
        help="Adds one normal working day in each week you pick, paid as "
             "overtime.")

df, k = simulate(cal, p)

# ------------------------------------------------------------------ KPIs
ok = lambda cond: "✅" if cond else "⚠️"
NO_ARROW = dict(delta_color="off", delta_arrow="off")
stock_ok = (k["not_delivered"] < 1 and k["stockout_weeks"] == 0)
share_profit = k["cost_per_order"] / NET_PROFIT_PER_ORDER
pl, plk = pnl(p, k)

c = st.columns(5)
c[0].metric("Bottleneck", k["bottleneck"],
            f"cycle time {k['cycle']:.2f} min/order", **NO_ARROW,
            help="The slowest stage sets the pace of the whole warehouse. "
                 "Cycle time = minutes between two finished orders there.")
c[1].metric(f"{ok(k['max_backlog_days'] <= p['policy'])} Max backlog",
            f"{k['max_backlog']:,.0f} orders",
            f"{k['max_backlog_days']:.1f} days (promise {p['policy']})",
            **NO_ARROW,
            help="Most orders waiting at the end of a week, and the working "
                 "days needed to clear them.")
c[2].metric(f"{ok(k['not_by_xmas'] < 1)} Not dispatched before Christmas",
            f"{k['not_by_xmas']:,.0f} orders",
            help="Orders still waiting at the end of December week 3, the "
                 "last week to dispatch for Christmas.")
c[3].metric(f"{ok(k['weeks_over'] == 0)} Weeks over promise",
            k["weeks_over"], f"{k['weeks_short']} weeks takt < cycle",
            **NO_ARROW,
            help="Weeks where the backlog is longer than the promise. Below: "
                 "weeks where orders arrive faster than the bottleneck "
                 "finishes them.")
c[4].metric(f"{ok(stock_ok)} Stockroom space (peak)",
            f"{k['stock_m2_peak']:,.0f} m²",
            f"of {k['area']['Stock']:,.0f} m² · "
            f"{k['cut']:,.0f} postponed", **NO_ARROW,
            help="Highest space the stock uses. Deliveries never exceed the "
                 "free space: what does not fit is postponed to the next "
                 "delivery. ✅ when all the stock arrives and picking never "
                 "runs out.")

c = st.columns(5)
c[0].metric("Orders this season", f"{k['total']:,.0f}",
            f"peak week {k['peak']:,.0f}", **NO_ARROW,
            help="Forecast from the demand drivers, and the busiest week.")
c[1].metric("Cost vs 2009 (− = saving)", money(k["extra_cost"]),
            f"{money(k['cost_per_order'], 2)} per order", **NO_ARROW,
            help="Overtime + staff difference vs the 2009 set-up, with the "
                 "case wages. Below: spread over the forecast orders.")
c[2].metric("Overtime cost", money(k["ot_cost"]),
            help="Overtime and Saturday hours × people × wage × "
                 "(1 + premium).")
c[3].metric("Staff cost vs 2009", money(k["staff_cost"]),
            help="(People now − people in 2009) × normal season hours × "
                 "wage. Negative = saving.")
c[4].metric(f"{ok(plk['net'] >= NET_PROFIT_PER_ORDER)} Net profit per parcel",
            money(plk["net"], 2),
            f"2009: £{NET_PROFIT_PER_ORDER:.2f}", **NO_ARROW,
            help="Appendix 1 recalculated for this scenario (see the Costs "
                 "tab). ✅ when it is at least the 2009 net profit per "
                 "parcel.")
if k["stockout_weeks"]:
    st.warning(f"⚠️ Out of stock in {k['stockout_weeks']} week(s): picking "
               "had orders but no products.")
if k["not_delivered"] >= 1:
    st.warning(f"⚠️ {k['not_delivered']:,.0f} orders' worth of stock never "
               "arrived: there was no free space or no re-order left. Add "
               "re-orders or give stock more m².")


def fig(title, ytitle, h=360):
    f = go.Figure()
    f.update_layout(title=title, yaxis_title=ytitle, height=h,
                    margin=dict(l=10, r=10, t=50, b=10), bargap=0.25,
                    legend=dict(orientation="h", y=-0.3),
                    hovermode="x unified")
    f.update_xaxes(showgrid=False)
    for i in [i for i, o in enumerate(df["open"]) if o == 0]:
        f.add_vrect(x0=i - 0.5, x1=i + 0.5, fillcolor=C_MUTED, opacity=0.18,
                    line_width=0, annotation_text="Closed",
                    annotation_position="top left", annotation_font_size=11)
    return f


x = df["label"]
tab1, tab_sp, tab2, tab_c, tab3 = st.tabs(["Capacity & bottleneck",
                                           "Space & people", "Weekly flow",
                                           "Costs", "Scenarios"])

with tab_c:
    st.markdown("#### Cost per parcel: 2009 (Appendix 1) vs new scenario")
    st.caption("One order = one parcel. Negative = cost. Totals = per parcel × "
               f"orders ({ORDERS_09:,} in 2009, {k['total']:,.0f} now).")
    bold = {"GROSS PROFIT", "NET PROFIT"}
    sty = (pl.style.format({c_: "{:,.2f}" for c_ in pl.columns if c_ != "Line"})
           .apply(lambda r: ["font-weight: bold" if r["Line"] in bold else ""
                             for _ in r], axis=1))
    st.dataframe(sty, width="stretch", hide_index=True,
                 height=(len(pl) + 1) * 35 + 3, column_config={
        "New scenario (£/parcel)": st.column_config.Column(
            help="Each line recalculated with the levers on the left."),
    })
    risk = k["not_by_xmas"] * plk["sales"]
    c = st.columns(3)
    c[0].metric(f"{ok(risk < 1)} Sales at risk", money(risk),
                f"{k['not_by_xmas']:,.0f} orders not dispatched before "
                "Christmas", **NO_ARROW,
                help="Value of the orders still waiting when the warehouse "
                     "closes. Not counted as lost in the table, because the "
                     "case does not say how many customers would cancel.")
    c[1].metric("Net profit, season", money(pl.iloc[-1]["New scenario total (£)"]),
                f"2009: {money(pl.iloc[-1]['2009 total (£)'])}", **NO_ARROW,
                help="Net profit per parcel × orders of the season.")
    c[2].metric("Fixed costs per parcel", money(FIXED_TOTAL / k["total"], 2),
                f"2009: £{P09['fixed']:.2f}", **NO_ARROW,
                help="Fixed costs stay at £227,808 in total; more orders "
                     "spread them thinner.")
    with st.expander("How each line is calculated"):
        st.markdown(
            f"- **Sales:** £42 per order, £{APP_ORDER_VALUE:.0f} for app "
            "orders (case trial), weighted by the app share.\n"
            "- **Cost of stock, packaging, carriage:** same share of sales as "
            "in 2009 (23 ÷ 42 = 54.8%).\n"
            "- **Loss of supplier discounts:** 8% of gross profit when there "
            "is more than one re-order (2009 had one).\n"
            f"- **Fixed costs:** £{FIXED_TOTAL:,.0f} in total (10.08 × 22,600), "
            "constant, ÷ orders.\n"
            f"- **Catalogue and web:** £{CAT_PER_MAILING:.2f} per catalogue "
            "mailed (3.25 × 22,600 ÷ 160,000) × (160,000 + new names), plus "
            "the extra advertising, ÷ orders.\n"
            "- **Wages:** the 2009 cost per parcel of each stage, plus that "
            "stage's overtime and staff difference vs 2009 (including idle "
            "time) ÷ orders.\n"
            "- **Not included:** remote recording keeps the same pay; late "
            "orders are shown as sales at risk, not as losses.")

with tab1:
    l, r = st.columns(2)
    f = go.Figure()
    caps = k["normal_cap"]
    f.add_bar(y=STAGES, x=[caps[s] for s in STAGES], orientation="h",
              marker_color=[STAGE_COLOR[s] for s in STAGES],
              text=[f"{caps[s]:,.0f}" for s in STAGES], textposition="auto",
              hovertemplate="%{y}: %{x:,.0f} orders/week<extra></extra>",
              showlegend=False)
    f.add_vline(x=k["peak"], line_dash="dash", line_color=C_CRITICAL,
                annotation_text=f"Peak week {k['peak']:,.0f}",
                annotation_position="top")
    f.update_layout(title="Weekly capacity by stage vs peak week demand",
                    xaxis_title="orders / week", height=360,
                    margin=dict(l=10, r=10, t=50, b=10))
    f.update_yaxes(autorange="reversed")
    l.plotly_chart(f, width="stretch")
    l.caption("Normal week, no overtime. The shortest bar is the "
              "bottleneck; any bar left of the red line cannot keep up at "
              "the peak.")

    f = fig("Takt time vs cycle time", "minutes per order")
    f.add_scatter(x=x, y=df["takt"], name="Takt (an order arrives every…)",
                  mode="lines+markers", line=dict(color=C_ORDERS, width=2))
    f.add_scatter(x=x, y=df["cycle"], name="Cycle (we finish one every…)",
                  mode="lines", line=dict(color=C_CAPACITY, width=2,
                                          dash="dash"))
    short = df["takt"] < df["cycle"]
    f.add_scatter(x=x[short], y=df.loc[short, "takt"], mode="markers",
                  name="Takt < cycle (backlog grows)",
                  marker=dict(color=C_CRITICAL, size=11, symbol="x"))
    f.update_yaxes(range=[0, max(4, (df["cycle"].max() or 1) * 2.5)])
    r.plotly_chart(f, width="stretch")
    r.caption("When the blue line drops below the orange one, orders arrive "
              "faster than the bottleneck can finish them.")

    st.markdown("#### How the cycle time of each stage is calculated")
    st.caption("Orders per hour of the stage = people × orders per person "
               "per hour. Cycle time = 60 ÷ orders per hour of the stage. "
               "Weekly capacity = orders per hour × normal hours per week.")
    sh = k["shift_h"]
    st.dataframe(pd.DataFrame({
        "People": [p["people"][s] for s in STAGES],
        "Base rate (orders/person/h)": [p["rate"][s] for s in STAGES],
        "Rate used (orders/person/h)": [round(k["rate"][s], 2)
                                        for s in STAGES],
        "Orders/h (stage)": [round(k["per_hour"][s], 1) for s in STAGES],
        "Cycle time (min)": [round(60 / k["per_hour"][s], 2) for s in STAGES],
        "Hours/day": [sh[s] for s in STAGES],
        "Days/week": [p["days"]] * 4,
        "Weekly capacity": [round(k["normal_cap"][s]) for s in STAGES],
        "Utilisation": [f"{k['util'][s]:.0%}" for s in STAGES],
    }, index=STAGES), width="stretch", column_config={
        "Base rate (orders/person/h)": st.column_config.NumberColumn(
            help="Productivity from the case (or what you set)."),
        "Rate used (orders/person/h)": st.column_config.NumberColumn(
            help="Same as the base rate, except recording: it mixes typed "
                 "orders (22/h) with app orders (60/h) by the app share."),
        "Orders/h (stage)": st.column_config.NumberColumn(
            help="People × rate used."),
        "Cycle time (min)": st.column_config.NumberColumn(
            help="60 ÷ orders per hour of the stage. The highest is the "
                 "bottleneck and sets the cycle time of the whole process."),
        "Weekly capacity": st.column_config.NumberColumn(
            help="Orders per hour × hours per day × days per week, without "
                 "overtime."),
        "Utilisation": st.column_config.TextColumn(
            help="Share of the capacity used during the season."),
    })

    st.markdown("#### How the takt time of each week is calculated")
    st.caption("Available minutes = (days worked × hours per day, including "
               "overtime) × 60, for the bottleneck stage of that week. "
               "Takt time = available minutes ÷ orders of the week. If takt "
               "is lower than cycle time, the backlog grows.")
    tk = df[df["orders"].gt(0) & df["open"].eq(1)].copy()
    tk["hours_day"] = [k["shift_h"][b] + o for b, o in
                       zip(tk["bott_stage"], tk["ot_day"])]
    tk["gap"] = tk["takt"] - tk["cycle"]
    tk["orders"] = tk["orders"].round(0)
    tk["status"] = ["OK" if g >= 0 else "Backlog grows" for g in tk["gap"]]
    cols = {"label": "Week", "orders": "Orders", "bott_stage": "Bottleneck",
            "days_w": "Days worked", "hours_day": "Hours/day",
            "minutes_bott": "Available minutes", "takt": "Takt (min)",
            "cycle": "Cycle (min)", "gap": "Takt − cycle", "status": "Status"}
    st.dataframe(tk[list(cols)].round(2).rename(columns=cols),
                 width="stretch", hide_index=True, column_config={
        "Days worked": st.column_config.NumberColumn(
            help="Normal days plus one if Saturday is open for this stage."),
        "Hours/day": st.column_config.NumberColumn(
            help="Normal hours of the bottleneck stage plus its overtime."),
        "Available minutes": st.column_config.NumberColumn(
            help="Days worked × hours per day × 60, plus Saturday hours."),
        "Takt (min)": st.column_config.NumberColumn(
            help="Available minutes ÷ orders: an order arrives every … "
                 "minutes."),
        "Cycle (min)": st.column_config.NumberColumn(
            help="60 ÷ orders per hour of the bottleneck: we finish an order "
                 "every … minutes."),
    })

with tab_sp:
    a = k["area"]
    used = {"Packing": p["people"]["Packing"] * p["m2_packer"],
            "Dispatch": p["people"]["Dispatch"] * p["m2_dispatcher"],
            "Stock": k["stock_m2_peak"]}
    l, r = st.columns(2)
    f = go.Figure()
    f.add_bar(x=list(used), y=[a[z] for z in used], name="Available",
              marker_color=C_ORDERS)
    f.add_bar(x=list(used), y=list(used.values()), name="Used / needed",
              marker_color=C_CAPACITY)
    f.update_layout(title="Space plan (m²)", yaxis_title="m²", height=360,
                    barmode="group", margin=dict(l=10, r=10, t=50, b=10),
                    legend=dict(orientation="h", y=-0.2))
    l.plotly_chart(f, width="stretch")

    peak_rate = k["peak"] / (p["days"] * p["hours_wh"])
    need = {z: -(-peak_rate // k["rate"][z]) for z in ["Packing", "Dispatch"]}
    r.markdown("#### What the peak week would need (normal hours)")
    r.markdown(
        f"- Peak week: **{k['peak']:,.0f} orders** → "
        f"**{peak_rate:,.0f} orders/hour**\n"
        f"- Packers: **{need['Packing']:.0f}** → "
        f"**{need['Packing'] * p['m2_packer']:,.0f} m²** "
        f"(you have {a['Packing']:,.0f} m²)\n"
        f"- Dispatchers: **{need['Dispatch']:.0f}** → "
        f"**{need['Dispatch'] * p['m2_dispatcher']:,.0f} m²** "
        f"(you have {a['Dispatch']:,.0f} m²)\n"
        f"- Stock at its peak: "
        f"**{k['stock_m2_peak']:,.0f} m²** (you have {a['Stock']:,.0f} m²)")

    st.markdown("#### Room for more people")
    rows = []
    for z, per in [("Packing", p["m2_packer"]),
                   ("Dispatch", p["m2_dispatcher"])]:
        n_ = p["people"][z]
        rows.append({"Area": z, "Area (m²)": a[z], "m² per person": per,
                     "Max people": max_people[z], "People now": n_,
                     "m² used": round(n_ * per), "Free m²":
                     round(a[z] - n_ * per),
                     "People you can add": max_people[z] - n_})
    rows.append({"Area": "Recording", "Area (m²)": None,
                 "m² per person": None, "Max people": max_people["Recording"],
                 "People now": p["people"]["Recording"], "m² used": None,
                 "Free m²": None, "People you can add":
                 max_people["Recording"] - p["people"]["Recording"]})
    rows.append({"Area": "Stock", "Area (m²)": a["Stock"],
                 "m² per person": None, "Max people": None,
                 "People now": None, "m² used": round(k["stock_m2_peak"]),
                 "Free m²": round(a["Stock"] - k["stock_m2_peak"]),
                 "People you can add": None})
    fmt = lambda v: ("–" if v is None else
                     f"{v:,.1f}" if isinstance(v, float) and v % 1
                     else f"{v:,.0f}" if isinstance(v, (int, float)) else v)
    room = pd.DataFrame([{c_: fmt(v_) for c_, v_ in r_.items()}
                         for r_ in rows])
    st.dataframe(room, width="stretch", hide_index=True,
                 column_config={
        "Max people": st.column_config.TextColumn(
            help="Area ÷ m² per person (desks for recording)."),
        "Free m²": st.column_config.TextColumn(
            help="Area not used by the people working there now. For "
                 "stock: area minus the space the stock needs at its peak "
                 "(negative = it does not fit)."),
        "People you can add": st.column_config.TextColumn(
            help="Max people − people now. To add more, give the area more "
                 "m² in section 1."),
    })
    st.caption("Picking has no space limit: collectors work in the "
               "stockroom aisles.")

    st.markdown("#### Stock replenishment")
    st.caption(f"Season stock to buy: {k['total']:,.0f} forecast orders × "
               f"(1 {k['margin']:+.1%}) = **{k['bought']:,.0f} orders' worth**. "
               f"Space per order: {k['m2_unit']:.4f} m², so the stockroom "
               f"holds **{a['Stock'] / k['m2_unit']:,.0f} orders' worth**. "
               + f"First delivery in Oct w1, then {p['n_follow']} re-order(s) "
                 f"every {p['interval']} weeks."
               + (f" {k['late_reorders']} re-order(s) would arrive after the "
                  "season and are not received." if k["late_reorders"] else ""))
    if k["deliveries"]:
        st.dataframe(pd.DataFrame(k["deliveries"]).round(0), width="stretch",
                     hide_index=True, column_config={
            "Planned": st.column_config.NumberColumn(
                help="Orders' worth this delivery should bring."),
            "Delivered": st.column_config.NumberColumn(
                help="What fitted in the free stockroom space."),
            "Cut by space": st.column_config.NumberColumn(
                help="Did not fit; moved to the next delivery."),
        })

with tab2:
    l, r = st.columns(2)
    f = fig("Orders vs capacity", "orders / week")
    f.add_bar(x=x, y=df["orders"], name="Orders in", marker_color=C_ORDERS)
    f.add_scatter(x=x, y=df["dispatched"], name="Dispatched",
                  mode="lines+markers", marker=dict(size=8),
                  line=dict(color=C_DONE, width=2))
    f.add_scatter(x=x, y=df["capacity"], name="Capacity (bottleneck)",
                  mode="lines", line=dict(color=C_CAPACITY, width=2,
                                          dash="dash", shape="hvh"))
    l.plotly_chart(f, width="stretch")
    l.caption("Grey band = closed for Christmas. Orders still waiting then "
              "go out in January.")

    f = fig("Where the backlog waits (queue in front of each stage)",
            "orders waiting")
    for s in STAGES:
        f.add_bar(x=x, y=df[f"q_{s}"], name=s, marker_color=STAGE_COLOR[s])
    f.update_layout(barmode="stack")
    r.plotly_chart(f, width="stretch")

    l, r = st.columns(2)
    f = fig("Backlog in days of work", "days")
    f.add_bar(x=x, y=df["backlog_days"], name="Backlog (days)",
              marker_color=C_ORDERS)
    f.add_hline(y=p["policy"], line_dash="dot", line_color=C_CRITICAL,
                annotation_text=f"Promise {p['policy']} days")
    l.plotly_chart(f, width="stretch")

    f = fig("Stock", "orders' worth of stock")
    f.add_scatter(x=x, y=df["stock"], name="Stock", mode="lines",
                  fill="tozeroy", line=dict(color=C_ORDERS, width=2))
    f.add_bar(x=x, y=df["entry"], name="Delivery", marker_color=C_DONE)
    f.add_scatter(x=x, y=df["stock_cap"], name="Stockroom capacity",
                  mode="lines", line=dict(color=C_CRITICAL, width=2,
                                          dash="dash"))
    f.add_scatter(x=x, y=df["min_stock"], name="Needed for next 2 weeks",
                  mode="lines", line=dict(color=C_CAPACITY, width=2,
                                          dash="dot"))
    r.plotly_chart(f, width="stretch")

    with st.expander("Weekly table"):
        names = {
            "label": ("Week", "Month and week."),
            "orders": ("Orders in", "New orders this week."),
            "capacity": ("Capacity", "What the bottleneck can finish."),
            "dispatched": ("Dispatched", "Orders that left the warehouse."),
            "backlog": ("Backlog", "Orders waiting at the end of the week."),
            "backlog_days": ("Backlog (days)", "Working days to clear it."),
            "takt": ("Takt (min)", "Minutes between two incoming orders."),
            "cycle": ("Cycle (min)", "Minutes between two finished orders."),
            "entry": ("Stock in", "Products delivered, in orders' worth."),
            "stock": ("Stock", "Products left at the end of the week."),
        }
        st.dataframe(
            df[list(names)].round(2).rename(
                columns={c_: n_ for c_, (n_, _) in names.items()}),
            width="stretch", hide_index=True,
            column_config={n_: st.column_config.Column(help=h_)
                           for n_, h_ in names.values()})

with tab3:
    st.markdown("Save the current set-up and compare it with others.")
    name = st.text_input(
        "Scenario name",
        value=f"Scenario {len(st.session_state.get('scen', [])) + 1}",
        help="A short name, e.g. '200 m² packing + Saturdays'.")
    if st.button("💾 Save scenario",
                 help="Adds the current levers and results to the table."):
        st.session_state.setdefault("scen", []).append({
            "Scenario": name,
            "m² packing/dispatch/stock": f"{a['Packing']:.0f}/"
                                         f"{a['Dispatch']:.0f}/"
                                         f"{a['Stock']:.0f}",
            "People R/Pi/Pa/D": "/".join(str(p["people"][s]) for s in STAGES),
            "App share": f"{p['app_share']:.0%}",
            "Overtime weeks": len(p["ot_weeks"]),
            "Saturdays": len(p["sat_weeks"]),
            "Nov→Oct": f"{p['shift']:.0%}",
            "Other growth": f"{p['growth']:.0%}",
            "Bottleneck": k["bottleneck"],
            "Max backlog (days)": round(k["max_backlog_days"], 1),
            "Not by Christmas": round(k["not_by_xmas"]),
            "Value per order (£)": p["unit_value"],
            "Re-orders": p["n_follow"],
            "Weeks between": p["interval"],
            "Remote recording": "yes" if p["remote_rec"] else "no",
            "Stock postponed": round(k["cut"]),
            "Stock not delivered": round(k["not_delivered"]),
            "Cost vs 2009 (£)": round(k["extra_cost"]),
            "Cost per order (£)": round(k["cost_per_order"], 2),
            "Net profit per parcel (£)": round(plk["net"], 2),
            "Sales at risk (£)": round(k["not_by_xmas"] * plk["sales"]),
        })
    scen = pd.DataFrame(st.session_state.get("scen", []))
    if len(scen):
        st.dataframe(scen, width="stretch", hide_index=True)
        st.download_button("Download as CSV", scen.to_csv(index=False),
                           file_name="ice_house_scenarios.csv",
                           mime="text/csv",
                           help="Open the table in Excel or Google Sheets.")
        if st.button("Clear scenarios", help="Deletes all saved scenarios."):
            st.session_state["scen"] = []
            st.rerun()
