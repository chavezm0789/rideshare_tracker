import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, date

# ------------------------------------------------------------
# VERSION STAMP
# ------------------------------------------------------------
APP_VERSION = "v2026-02-22_true-cost_prod_01"

# ------------------------------------------------------------
# STREAMLIT CONFIG
# ------------------------------------------------------------
st.set_page_config(page_title="Rideshare Income Tracker", layout="wide")
st.title("Rideshare Income Tracker")
st.caption(f"Running: {APP_VERSION}")

# ------------------------------------------------------------
# SESSION STATE
# ------------------------------------------------------------
if "active_shift" not in st.session_state:
    st.session_state["active_shift"] = None

# ------------------------------------------------------------
# DB CONNECTION
# ------------------------------------------------------------
def _stop_missing_secrets():
    st.error("No DB secrets found. Add them in Streamlit Cloud → Settings → Secrets.")
    st.stop()

@st.cache_resource
def get_conn():
    if "db" not in st.secrets or "dsn" not in st.secrets["db"]:
        _stop_missing_secrets()

    dsn = st.secrets["db"]["dsn"]

    # Force SSL if missing
    if "sslmode=" not in dsn:
        dsn += "&sslmode=require" if "?" in dsn else "?sslmode=require"

    try:
        conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
        conn.autocommit = True
        return conn
    except Exception as e:
        st.error(f"Database connection failed: {e}")
        st.stop()

def init_db():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("create extension if not exists pgcrypto;")

        cur.execute("""
        create table if not exists public.shifts (
            id uuid primary key default gen_random_uuid(),
            created_at timestamptz not null default now(),

            shift_date date not null,
            platform text not null,
            shift_label text,

            start_ts timestamptz,
            end_ts timestamptz,
            start_time text,
            end_time text,
            online_hours numeric,

            gross_fares numeric,
            in_app_tips numeric,
            bonuses numeric,
            cash_tips numeric,
            total_income numeric,

            miles numeric,
            rides integer,
            notes text,

            hourly_rate numeric
        );
        """)

        cur.execute("""
        create table if not exists public.expenses (
            id uuid primary key default gen_random_uuid(),
            created_at timestamptz not null default now(),

            exp_date date not null,
            category text not null,
            description text,
            amount numeric not null,

            business_use_pct integer not null default 100,
            deductible_amount numeric not null default 0,

            notes text
        );
        """)

try:
    init_db()
except Exception as e:
    st.error(f"Database init failed: {e}")
    st.stop()

def weighted_rate(numerator: float, denominator: float) -> float:
    return (numerator / denominator) if denominator and denominator > 0 else 0.0

def load_shifts() -> pd.DataFrame:
    conn = get_conn()
    query = """
        select
            id, created_at,
            shift_date, platform, shift_label,
            start_ts, end_ts, start_time, end_time, online_hours,
            gross_fares, in_app_tips, bonuses, cash_tips, total_income,
            miles, rides, notes, hourly_rate
        from public.shifts
        where shift_date is not null
          and platform is not null
          and trim(lower(platform)) <> 'platform'
        order by shift_date desc, created_at desc;
    """
    return pd.read_sql_query(query, conn)

def load_expenses() -> pd.DataFrame:
    conn = get_conn()
    query = """
        select
            id, created_at,
            exp_date, category, description, amount,
            business_use_pct, deductible_amount, notes
        from public.expenses
        order by exp_date desc, created_at desc;
    """
    return pd.read_sql_query(query, conn)

def insert_shift(row: dict) -> None:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into public.shifts (
                shift_date, platform, shift_label,
                start_ts, end_ts, start_time, end_time, online_hours,
                gross_fares, in_app_tips, bonuses, cash_tips, total_income,
                miles, rides, notes, hourly_rate
            ) values (
                %(shift_date)s, %(platform)s, %(shift_label)s,
                %(start_ts)s, %(end_ts)s, %(start_time)s, %(end_time)s, %(online_hours)s,
                %(gross_fares)s, %(in_app_tips)s, %(bonuses)s, %(cash_tips)s, %(total_income)s,
                %(miles)s, %(rides)s, %(notes)s, %(hourly_rate)s
            );
            """,
            row,
        )

def insert_expense(row: dict) -> None:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into public.expenses (
                exp_date, category, description, amount,
                business_use_pct, deductible_amount, notes
            ) values (
                %(exp_date)s, %(category)s, %(description)s, %(amount)s,
                %(business_use_pct)s, %(deductible_amount)s, %(notes)s
            );
            """,
            row,
        )

tabs = st.tabs(["🚗 Log Shift", "💸 Log Expense", "📊 Dashboard"])

# ---------------- TAB 1: LOG SHIFT ----------------
with tabs[0]:
    st.subheader("Log a Driving Shift")

    active = st.session_state["active_shift"]

    if active is None:
        st.markdown("### ⏱ Start a Shift")

        c1, c2 = st.columns(2)
        with c1:
            shift_date = st.date_input("Date", value=date.today(), key="t1_shift_date")
            platform = st.selectbox("Platform", ["Lyft", "Uber", "Both", "Other"], key="t1_platform")
            shift_label = st.text_input("Shift label (optional)", key="t1_label")
        with c2:
            pre_notes = st.text_area("Notes (optional)", key="t1_notes")

        if st.button("Start Shift", key="t1_start_btn"):
            start_dt = datetime.now()
            st.session_state["active_shift"] = {
                "shift_date": shift_date,
                "platform": platform,
                "shift_label": shift_label,
                "notes": pre_notes,
                "start_ts": start_dt,
                "status": "awaiting_start_odo",
            }
            st.success("Shift started.")
            st.rerun()
    else:
        status = active.get("status", "running")
        start_ts: datetime = active["start_ts"]

        if status == "awaiting_start_odo":
            st.markdown("### Enter Start Odometer (when safe)")
            st.info(
                f"Shift date: **{active['shift_date']}** · Platform: **{active['platform']}** · "
                f"Started: **{start_ts.strftime('%H:%M')}**"
            )

            start_odo = st.number_input("Odometer at START", min_value=0.0, step=1.0, key="t1_start_odo")

            b1, b2 = st.columns(2)
            with b1:
                if st.button("Save Start Mileage", key="t1_save_start_odo"):
                    st.session_state["active_shift"]["start_odo"] = float(start_odo)
                    st.session_state["active_shift"]["status"] = "running"
                    st.rerun()
            with b2:
                if st.button("Cancel Shift", key="t1_cancel_1"):
                    st.session_state["active_shift"] = None
                    st.rerun()

        elif status == "running":
            elapsed_hours = round((datetime.now() - start_ts).total_seconds() / 3600, 2)
            start_odo = float(active.get("start_odo", 0.0))

            st.markdown("### Shift In Progress")
            st.info(
                f"Started: **{start_ts.strftime('%H:%M')}** · Elapsed: **{elapsed_hours:.2f}h**"
                + (f" · Start odometer: **{start_odo:.0f}**" if start_odo > 0 else "")
            )

            b1, b2 = st.columns(2)
            with b1:
                if st.button("End Shift", key="t1_end_btn"):
                    st.session_state["active_shift"]["end_ts"] = datetime.now()
                    st.session_state["active_shift"]["status"] = "awaiting_end_odo"
                    st.rerun()
            with b2:
                if st.button("Cancel Shift (don’t save)", key="t1_cancel_2"):
                    st.session_state["active_shift"] = None
                    st.rerun()

        elif status == "awaiting_end_odo":
            end_ts: datetime = active["end_ts"]
            online_hours = round((end_ts - start_ts).total_seconds() / 3600, 2)
            start_odo = float(active.get("start_odo", 0.0))

            st.markdown("### ✅ Finish Shift")
            st.info(
                f"Start: **{start_ts.strftime('%H:%M')}** · End: **{end_ts.strftime('%H:%M')}** · "
                f"Online: **{online_hours:.2f}h**"
            )

            c1, c2 = st.columns(2)
            with c1:
                st.write(f"Start odometer: **{start_odo:.0f}**")
                end_odo = st.number_input("Odometer at END", min_value=0.0, step=1.0, key="t1_end_odo")
                rides = st.number_input("Rides Completed", min_value=0, step=1, key="t1_rides")
            with c2:
                gross = st.number_input("Gross Fares", min_value=0.0, step=1.0, key="t1_gross")
                tips = st.number_input("In-App Tips", min_value=0.0, step=1.0, key="t1_tips")
                bonuses = st.number_input("Bonuses", min_value=0.0, step=1.0, key="t1_bonus")
                cash = st.number_input("Cash Tips", min_value=0.0, step=1.0, key="t1_cash")

            notes = st.text_area("Notes (optional)", value=active.get("notes", ""), key="t1_finish_notes")

            if start_odo > 0 and end_odo < start_odo:
                st.error("End odometer is less than start. Check your inputs.")
                miles = 0.0
            else:
                miles = round(max(end_odo - start_odo, 0.0), 1) if start_odo > 0 else 0.0

            total_income = round(gross + tips + bonuses + cash, 2)
            hourly_rate = round(weighted_rate(total_income, online_hours), 2)

            st.write(f"**Miles (calculated):** {miles:.1f}")
            st.write(f"**Total income:** ${total_income:.2f}")
            st.write(f"**Hourly rate (gross):** ${hourly_rate:.2f}/hr")

            b1, b2 = st.columns(2)
            with b1:
                if st.button("Save Shift", key="t1_save_shift"):
                    row = {
                        "shift_date": active["shift_date"],
                        "platform": active["platform"],
                        "shift_label": active.get("shift_label", ""),
                        "start_ts": start_ts,
                        "end_ts": end_ts,
                        "start_time": start_ts.strftime("%H:%M"),
                        "end_time": end_ts.strftime("%H:%M"),
                        "online_hours": online_hours,
                        "gross_fares": gross,
                        "in_app_tips": tips,
                        "bonuses": bonuses,
                        "cash_tips": cash,
                        "total_income": total_income,
                        "miles": miles,
                        "rides": int(rides),
                        "notes": notes,
                        "hourly_rate": hourly_rate,
                    }
                    insert_shift(row)
                    st.session_state["active_shift"] = None
                    st.success(f"Shift saved. Gross hourly: ${hourly_rate:.2f}/hr")
                    st.rerun()
            with b2:
                if st.button("Cancel (don’t save)", key="t1_cancel_3"):
                    st.session_state["active_shift"] = None
                    st.rerun()

    st.markdown("---")
    st.subheader("Recent Shifts")
    shifts_df = load_shifts()
    if len(shifts_df) == 0:
        st.info("No shifts logged yet.")
    else:
        show = shifts_df.copy()
        show["shift_date"] = pd.to_datetime(show["shift_date"], errors="coerce").dt.date
        # show useful columns first
        cols = ["shift_date", "platform", "online_hours", "total_income", "miles", "hourly_rate", "rides", "shift_label", "notes"]
        cols = [c for c in cols if c in show.columns]
        st.dataframe(show[cols].head(25), use_container_width=True)

# ---------------- TAB 2: LOG EXPENSE ----------------
with tabs[1]:
    st.subheader("Log an Expense")

    c1, c2 = st.columns(2)
    with c1:
        exp_date = st.date_input("Date", value=date.today(), key="t2_date")
        category = st.selectbox(
            "Category",
            ["Gas", "Maintenance", "Car Wash", "Parking/Tolls", "Insurance", "Phone", "Supplies", "Other"],
            key="t2_cat",
        )
        description = st.text_input("Description", key="t2_desc")
    with c2:
        amount = st.number_input("Amount", min_value=0.0, step=1.0, key="t2_amount")
        business_pct = st.slider("Business Use %", 0, 100, 100, key="t2_pct")
        deductible = round(amount * (business_pct / 100), 2)
        st.write(f"**Deductible amount:** ${deductible:.2f}")

    notes = st.text_area("Notes (optional)", key="t2_notes")

    if st.button("Save Expense", key="t2_save"):
        row = {
            "exp_date": exp_date,
            "category": category,
            "description": description,
            "amount": amount,
            "business_use_pct": int(business_pct),
            "deductible_amount": deductible,
            "notes": notes,
        }
        insert_expense(row)
        st.success("Expense saved.")
        st.rerun()

    st.markdown("---")
    st.subheader("Recent Expenses")
    expenses_df = load_expenses()
    if len(expenses_df) == 0:
        st.info("No expenses logged yet.")
    else:
        show = expenses_df.copy()
        show["exp_date"] = pd.to_datetime(show["exp_date"], errors="coerce").dt.date
        cols = ["exp_date", "category", "amount", "business_use_pct", "deductible_amount", "description", "notes"]
        cols = [c for c in cols if c in show.columns]
        st.dataframe(show[cols].head(25), use_container_width=True)

# ---------------- TAB 3: DASHBOARD ----------------
with tabs[2]:
    st.subheader("Dashboard")

    shifts_df = load_shifts()
    expenses_df = load_expenses()

    if len(shifts_df) == 0:
        st.info("Log at least one shift to see stats.")
        st.stop()

    shifts_df["shift_date"] = pd.to_datetime(shifts_df["shift_date"], errors="coerce")
    for col in ["total_income", "online_hours", "miles", "rides", "hourly_rate"]:
        shifts_df[col] = pd.to_numeric(shifts_df[col], errors="coerce").fillna(0)

    if len(expenses_df) > 0:
        expenses_df["exp_date"] = pd.to_datetime(expenses_df["exp_date"], errors="coerce")
        expenses_df["deductible_amount"] = pd.to_numeric(expenses_df["deductible_amount"], errors="coerce").fillna(0)
    else:
        expenses_df = pd.DataFrame(columns=["exp_date", "category", "deductible_amount"])
        expenses_df["exp_date"] = pd.to_datetime(expenses_df["exp_date"], errors="coerce")

    valid_dates = shifts_df["shift_date"].dropna()
    default_from = valid_dates.min().date() if len(valid_dates) else date.today()
    default_to = valid_dates.max().date() if len(valid_dates) else date.today()

    c1, c2, c3 = st.columns(3)
    with c1:
        start_date = st.date_input("From", value=default_from, key="t3_from")
    with c2:
        end_date = st.date_input("To", value=default_to, key="t3_to")
    with c3:
        platforms = sorted([p for p in shifts_df["platform"].dropna().unique().tolist()])
        platform_filter = st.multiselect("Platform", platforms, default=platforms, key="t3_platform")

    mask = (
        (shifts_df["shift_date"] >= pd.to_datetime(start_date)) &
        (shifts_df["shift_date"] <= pd.to_datetime(end_date)) &
        (shifts_df["platform"].isin(platform_filter))
    )
    s = shifts_df[mask].copy()

    if len(s) == 0:
        st.warning("No shifts match your filters.")
        st.stop()

    total_income = float(s["total_income"].sum())
    total_hours = float(s["online_hours"].sum())
    total_miles = float(s["miles"].sum())
    total_rides = float(s["rides"].sum())

    emask = (
        (expenses_df["exp_date"] >= pd.to_datetime(start_date)) &
        (expenses_df["exp_date"] <= pd.to_datetime(end_date))
    )
    e = expenses_df[emask].copy() if len(expenses_df) else pd.DataFrame(columns=["category", "deductible_amount"])

    total_expenses_logged = float(e["deductible_amount"].sum()) if len(e) else 0.0
    net_logged = total_income - total_expenses_logged

    gross_per_hour = weighted_rate(total_income, total_hours)
    net_per_hour_logged = weighted_rate(net_logged, total_hours)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Gross income", f"${total_income:,.2f}")
    m2.metric("Expenses (logged)", f"${total_expenses_logged:,.2f}")
    m3.metric("Net (logged)", f"${net_logged:,.2f}")
    m4.metric("Rides", f"{int(total_rides)}")

    m5, m6, m7 = st.columns(3)
    m5.metric("Gross per hour", f"${gross_per_hour:,.2f}")
    m6.metric("Net per hour (logged)", f"${net_per_hour_logged:,.2f}")
    m7.metric("Miles", f"{total_miles:,.1f}")

    st.markdown("---")
    st.subheader("True Cost (includes wear & tear)")

    EXTRA_CATS = {"Parking/Tolls", "Phone", "Supplies", "Other"}

    method = st.selectbox("True cost method", ["IRS mileage rate", "Custom per-mile model"], key="tc_method")

    if method == "IRS mileage rate":
        st.caption("Vehicle cost is estimated as miles × rate (bundles fuel + maintenance + depreciation).")
        rate = st.number_input("Mileage rate ($/mile)", min_value=0.0, step=0.01, value=0.67, key="tc_rate")

        vehicle_cost = total_miles * float(rate)
        extra_expenses = float(e[e["category"].isin(list(EXTRA_CATS))]["deductible_amount"].sum()) if len(e) else 0.0

        true_cost_total = vehicle_cost + extra_expenses
        true_net = total_income - true_cost_total
        true_per_hour = weighted_rate(true_net, total_hours)

        t1, t2, t3, t4 = st.columns(4)
        t1.metric("Estimated vehicle cost", f"${vehicle_cost:,.2f}")
        t2.metric("Extra expenses (add-on)", f"${extra_expenses:,.2f}")
        t3.metric("True cost net", f"${true_net:,.2f}")
        t4.metric("True cost per hour", f"${true_per_hour:,.2f}")

    else:
        st.caption("Custom model estimates depreciation + fuel + maintenance as a per-mile cost.")

        a1, a2, a3 = st.columns(3)
        with a1:
            purchase_price = st.number_input("Car purchase price ($)", 0.0, step=100.0, value=20000.0, key="tc_buy")
            resale_value = st.number_input("Estimated resale value ($)", 0.0, step=100.0, value=8000.0, key="tc_resale")
            lifetime_miles = st.number_input("Expected lifetime miles", 1.0, step=1000.0, value=200000.0, key="tc_life")
        with a2:
            mpg = st.number_input("MPG (average)", 1.0, step=0.5, value=25.0, key="tc_mpg")
            gas_price = st.number_input("Gas price ($/gal)", 0.0, step=0.05, value=3.50, key="tc_gas")
            maint_per_mile = st.number_input("Maintenance ($/mile)", 0.0, step=0.01, value=0.10, key="tc_maint")
        with a3:
            tires_per_mile = st.number_input("Tires ($/mile)", 0.0, step=0.01, value=0.02, key="tc_tires")
            misc_per_mile = st.number_input("Other ($/mile)", 0.0, step=0.01, value=0.03, key="tc_misc")
            include_extras = st.checkbox("Subtract logged extras too", value=True, key="tc_extras")

        depreciation_per_mile = max(purchase_price - resale_value, 0.0) / float(lifetime_miles)
        fuel_per_mile = (gas_price / mpg) if mpg else 0.0
        per_mile = float(depreciation_per_mile + fuel_per_mile + maint_per_mile + tires_per_mile + misc_per_mile)

        vehicle_cost = total_miles * per_mile
        extra_expenses = float(e[e["category"].isin(list(EXTRA_CATS))]["deductible_amount"].sum()) if (include_extras and len(e)) else 0.0

        true_cost_total = vehicle_cost + extra_expenses
        true_net = total_income - true_cost_total
        true_per_hour = weighted_rate(true_net, total_hours)

        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Depreciation ($/mile)", f"${depreciation_per_mile:,.3f}")
        b2.metric("Fuel ($/mile)", f"${fuel_per_mile:,.3f}")
        b3.metric("Total ($/mile)", f"${per_mile:,.3f}")
        b4.metric("Vehicle cost (period)", f"${vehicle_cost:,.2f}")

        c1, c2, c3 = st.columns(3)
        c1.metric("Extra expenses", f"${extra_expenses:,.2f}")
        c2.metric("True cost net", f"${true_net:,.2f}")
        c3.metric("True cost per hour", f"${true_per_hour:,.2f}")
