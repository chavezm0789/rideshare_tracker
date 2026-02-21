import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, date

# ------------------------------------------------------------
# STREAMLIT CONFIG
# ------------------------------------------------------------
st.set_page_config(page_title="Rideshare Income Tracker", layout="wide")
st.title("Rideshare Income Tracker")

# ------------------------------------------------------------
# SESSION STATE
# ------------------------------------------------------------
if "active_shift" not in st.session_state:
    st.session_state["active_shift"] = None


# ------------------------------------------------------------
# DB: Connection + Setup
# ------------------------------------------------------------
def _require_secrets():
    """
    Expect secrets in Streamlit format:

    [db]
    host = "..."
    port = 5432
    dbname = "postgres"
    user = "postgres"
    password = "..."
    sslmode = "require"

    OR

    [db]
    dsn = "postgresql://user:pass@host:5432/dbname?sslmode=require"
    """
    if "db" not in st.secrets:
        st.error("Missing [db] secrets. Add them in Streamlit Cloud → App settings → Secrets.")
        st.stop()


@st.cache_resource
def get_conn():
    _require_secrets()

    db = st.secrets["db"]

    if "dsn" in db and db["dsn"]:
        conn = psycopg2.connect(db["dsn"], cursor_factory=RealDictCursor)
    else:
        conn = psycopg2.connect(
            host=db["host"],
            port=int(db.get("port", 5432)),
            dbname=db.get("dbname", "postgres"),
            user=db["user"],
            password=db["password"],
            sslmode=db.get("sslmode", "require"),
            cursor_factory=RealDictCursor,
        )

    conn.autocommit = True
    return conn


def init_db():
    """
    Creates tables if they don't exist.
    We use pgcrypto's gen_random_uuid() for UUIDs.
    """
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
        create extension if not exists pgcrypto;
        """)

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


def weighted_rate(numerator: float, denominator: float) -> float:
    return (numerator / denominator) if denominator and denominator > 0 else 0.0


def qdf_to_float(x, default=0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


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


# Run DB setup once
init_db()

# ------------------------------------------------------------
# UI TABS
# ------------------------------------------------------------
tabs = st.tabs(["🚗 Log Shift", "💸 Log Expense", "📊 Dashboard"])


# ============================================================
# TAB 1: LOG SHIFT (Start -> start odo later -> End -> end odo + earnings)
# ============================================================
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
            st.write(f"**Hourly rate (this shift):** ${hourly_rate:.2f}/hr")

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
                    st.success(f"Shift saved. Hourly: ${hourly_rate:.2f}/hr")
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
        st.dataframe(show.head(20), use_container_width=True)


# ============================================================
# TAB 2: LOG EXPENSE
# ============================================================
with tabs[1]:
    st.subheader("Log an Expense")

    c1, c2 = st.columns(2)
    with c1:
        exp_date = st.date_input("Date", value=date.today(), key="t2_date")
        category = st.selectbox(
            "Category",
            ["Gas", "Maintenance", "Car Wash", "Parking/Tolls", "Insurance", "Phone", "Supplies", "Other"],
            key="t2_cat"
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
            "notes": notes
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
        st.dataframe(show.head(20), use_container_width=True)


# ============================================================
# TAB 3: DASHBOARD
# ============================================================
with tabs[2]:
    st.subheader("Dashboard")

    shifts_df = load_shifts()
    expenses_df = load_expenses()

    if len(shifts_df) == 0:
        st.info("Log at least one shift to see stats.")
        st.stop()

    # Clean types
    shifts_df["shift_date"] = pd.to_datetime(shifts_df["shift_date"], errors="coerce")
    for col in ["total_income", "online_hours", "miles", "rides", "hourly_rate"]:
        shifts_df[col] = pd.to_numeric(shifts_df[col], errors="coerce").fillna(0)

    if len(expenses_df) > 0:
        expenses_df["exp_date"] = pd.to_datetime(expenses_df["exp_date"], errors="coerce")
        expenses_df["deductible_amount"] = pd.to_numeric(expenses_df["deductible_amount"], errors="coerce").fillna(0)
    else:
        expenses_df = pd.DataFrame(columns=["exp_date", "deductible_amount"])

    # Filters
    c1, c2, c3 = st.columns(3)
    with c1:
        start_date = st.date_input("From", value=shifts_df["shift_date"].min().date(), key="t3_from")
    with c2:
        end_date = st.date_input("To", value=shifts_df["shift_date"].max().date(), key="t3_to")
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

    # Expenses filtered by date range (expenses aren't tied to platform)
    emask = (
        (expenses_df["exp_date"] >= pd.to_datetime(start_date)) &
        (expenses_df["exp_date"] <= pd.to_datetime(end_date))
    )
    total_expenses = float(expenses_df[emask]["deductible_amount"].sum()) if len(expenses_df) else 0.0

    net = total_income - total_expenses
    gross_per_hour = weighted_rate(total_income, total_hours)
    net_per_hour = weighted_rate(net, total_hours)
    net_per_mile = weighted_rate(net, total_miles)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Gross income", f"${total_income:,.2f}")
    m2.metric("Expenses (deductible)", f"${total_expenses:,.2f}")
    m3.metric("Net", f"${net:,.2f}")
    m4.metric("Rides", f"{int(total_rides)}")

    m5, m6, m7 = st.columns(3)
    m5.metric("Gross per hour", f"${gross_per_hour:,.2f}")
    m6.metric("Net per hour", f"${net_per_hour:,.2f}")
    m7.metric("Net per mile", f"${net_per_mile:,.2f}")

    st.markdown("---")
    st.subheader("Income over time")
    daily = (
        s.groupby(s["shift_date"].dt.date)["total_income"]
        .sum()
        .reset_index(name="total_income")
    )
    st.line_chart(daily.set_index("shift_date"))

    st.markdown("---")
    st.subheader("Hourly rate summaries (weighted)")

    s["week"] = s["shift_date"].dt.to_period("W").astype(str)
    s["month"] = s["shift_date"].dt.to_period("M").astype(str)
    s["year"] = s["shift_date"].dt.year.astype(int)

    def build_summary(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
        out = (
            df.groupby(group_col, as_index=False)
              .agg(total_income=("total_income", "sum"),
                   total_hours=("online_hours", "sum"))
        )
        out["hourly_rate"] = out.apply(
            lambda r: weighted_rate(float(r["total_income"]), float(r["total_hours"])),
            axis=1
        )
        out["total_income"] = out["total_income"].round(2)
        out["total_hours"] = out["total_hours"].round(2)
        out["hourly_rate"] = out["hourly_rate"].round(2)
        return out

    weekly = build_summary(s, "week").sort_values("week", ascending=False)
    monthly = build_summary(s, "month").sort_values("month", ascending=False)
    yearly = build_summary(s, "year").sort_values("year", ascending=False)

    cw, cm, cy = st.columns(3)
    with cw:
        st.markdown("#### Weekly")
        st.dataframe(weekly, use_container_width=True, height=300)
    with cm:
        st.markdown("#### Monthly")
        st.dataframe(monthly, use_container_width=True, height=300)
    with cy:
        st.markdown("#### Yearly")
        st.dataframe(yearly, use_container_width=True, height=300)