"""Build the 2-page submission PDF from the pipeline's own outputs (no hand-typed numbers).

    python docs/build_report_pdf.py        # run the pipeline first
"""
import math
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_JUSTIFY  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.platypus import (HRFlowable, Image, KeepTogether, Paragraph, SimpleDocTemplate,  # noqa: E402
                                Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "FDE_Assignment2_Report.pdf"
FIG = ROOT / "docs" / "figures"
AUTHOR = "Palak Agrawal"
REPO_URL = "https://github.com/palak348/FDE-Assignment/tree/main/assignment-2"

INK, GREY, LIGHT, ACCENT = (colors.HexColor(c) for c in ("#1b1b1b", "#5f5f5f", "#bdbdbd", "#1f3a5f"))
BLUE, ORANGE, GREY_BAR = "#2f6db3", "#d9642b", "#9a9a9a"

# ------------------------------------------------------------------ data
kpi = pd.read_csv(ROOT / "output" / "kpi_before_after.csv").set_index("metric_id")
hourly = pd.read_csv(ROOT / "output" / "cbd_speed_by_hour.csv")
con = duckdb.connect(str(ROOT / "data" / "warehouse" / "tlc.duckdb"), read_only=True)
days = dict(con.execute("select period, count(*) from dim_date where in_window group by 1").fetchall())
wet = dict(con.execute("select period, count(*) from dim_date where in_window and is_wet group by 1").fetchall())
total, not_trip, no_speed, usable = con.execute(
    "select count(*), count(*) filter (where not is_valid_trip), "
    "count(*) filter (where is_valid_trip and not speed_eligible), count(*) filter (where speed_eligible) "
    "from fact_trip_clean").fetchone()
window_days = con.execute("select period, day(day), is_wet from dim_date where in_window order by day").fetchall()
con.close()
net = kpi.loc["C2", "change_pct"] - kpi.loc["C4", "change_pct"]
chg = lambda m: kpi.loc[m, "change_pct"]
pct = lambda m: f"{chg(m):+.1f}%"
val = lambda m, side, f="{:.2f}": f.format(kpi.loc[m, side])
n_before, n_after = int(kpi.loc["M1", "n_before"]), int(kpi.loc["M1", "n_after"])

# ---------------------------------------------------------------- styles
MARGIN = 2.0 * cm
W = A4[0] - 2 * MARGIN - 12
base = dict(fontName="Times-Roman", fontSize=10, leading=13.4, textColor=INK)
st = lambda name, **kw: ParagraphStyle(name, **{**base, **kw})
S = {
    "title": st("title", fontName="Times-Bold", fontSize=17, leading=21),
    "meta": st("meta", fontSize=9.5, leading=12, textColor=GREY),
    "h": st("h", fontName="Times-Bold", fontSize=11.5, leading=14, textColor=ACCENT, spaceBefore=11, spaceAfter=4,
            keepWithNext=1),
    "p": st("p", alignment=TA_JUSTIFY, spaceAfter=5),
    "cap": st("cap", fontName="Times-Italic", fontSize=8.8, leading=11, textColor=GREY),
    "cell": st("cell", fontSize=8.9, leading=11),
    "cellb": st("cellb", fontName="Times-Bold", fontSize=8.9, leading=11),
}
P = lambda t, s="p": Paragraph(t, S[s])
H = lambda t: P(t, "h")


def caption(t, before=3, after=6):
    return [Spacer(1, before), P(t, "cap"), Spacer(1, after)]


def table(rows, widths, bold_first_col=False):
    """Booktabs style: rule above and below the header, rule at the bottom, nothing else."""
    data = [[P(c, "cellb") for c in rows[0]]] + \
           [[P(c, "cellb" if bold_first_col and j == 0 else "cell") for j, c in enumerate(r)] for r in rows[1:]]
    t = Table(data, colWidths=widths)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
        ("LINEABOVE", (0, 0), (-1, 0), 0.9, INK), ("LINEBELOW", (0, 0), (-1, 0), 0.5, INK),
        ("LINEBELOW", (0, -1), (-1, -1), 0.9, INK)]))
    return t


# ------------------------------------------------------------- diagram
def text(d, x, y, s, size=8, font="Times-Roman", color=INK, anchor="middle"):
    d.add(String(x, y, s, fontName=font, fontSize=size, fillColor=color, textAnchor=anchor))


def arrow(d, x1, y1, x2, y2, color=GREY):
    d.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=0.7))
    a = math.atan2(y2 - y1, x2 - x1)
    tip = [(x2, y2), (x2 - 4.5 * math.cos(a - 0.45), y2 - 4.5 * math.sin(a - 0.45)),
           (x2 - 4.5 * math.cos(a + 0.45), y2 - 4.5 * math.sin(a + 0.45))]
    d.add(Polygon([v for p in tip for v in p], fillColor=color, strokeColor=color))


def node(d, x, y, w, h, title, note, kind=None, heavy=False):
    d.add(Rect(x, y, w, h, fillColor=colors.white, strokeColor=INK if heavy else LIGHT,
               strokeWidth=1.1 if heavy else 0.7))
    top = y + h - 9.5
    if kind:
        text(d, x + w / 2, top, kind, 6.9, "Times-Italic", GREY)
        top -= 10
    text(d, x + w / 2, top, title, 8.6, "Times-Bold")
    text(d, x + w / 2, top - 9.8, note, 7.6, "Times-Roman", GREY)


def figure_model_pipeline():
    """Top: the workflow model (entities -> event <- intervention, event -> outcome). Bottom: the pipeline."""
    mh, gap_rows, ph = 2.6 * cm, 0.5 * cm, 1.05 * cm
    h = mh + gap_rows + ph
    d = Drawing(W, h)
    y0 = ph + gap_rows                       # bottom of the model row
    bh = 1.2 * cm                            # small boxes
    lw, tw, colgap = 3.6 * cm, 4.8 * cm, 1.1 * cm
    tx = lw + colgap
    rx, rw = tx + tw + colgap, W - (tx + tw + colgap)
    th = 1.35 * cm
    ty = y0 + (mh - th) / 2
    node(d, 0, y0 + mh - bh, lw, bh, "Zone", "265 zones, 38 tolled", "entity")
    node(d, 0, y0, lw, bh, "Day", "weekday, holiday, weather", "entity")
    node(d, tx, ty, tw, th, "Trip: pickup to dropoff", "times, distance, speed, fee paid", "event", heavy=True)
    node(d, rx, y0 + mh - bh, rw, bh, "Toll period", "before / after 5 January 2025", "intervention")
    node(d, rx, y0, rw, bh, "Speed metrics", "KPI plus crawl share and travel time", "outcome")
    arrow(d, lw + 2, y0 + mh - bh / 2, tx - 2, ty + th - 8)
    arrow(d, lw + 2, y0 + bh / 2, tx - 2, ty + 8)
    arrow(d, rx - 2, y0 + mh - bh / 2, tx + tw + 2, ty + th - 8)
    arrow(d, tx + tw + 2, ty + 8, rx - 2, y0 + bh / 2)

    steps = [("Retrieve", "retries, checks"), ("Raw store", "kept unchanged"),
             ("Load (SQL)", "DuckDB"), ("Validate", "13 row rules"),
             ("Quality gates", "5 per month + 2"), ("Metrics", "tables, charts")]
    pg = 0.3 * cm
    pw = (W - pg * (len(steps) - 1)) / len(steps)
    for i, (t, n) in enumerate(steps):
        px = i * (pw + pg)
        node(d, px, 0, pw, ph, t, n, heavy=t == "Quality gates")
        if i < len(steps) - 1:
            arrow(d, px + pw + 1, ph / 2, px + pw + pg - 1, ph / 2)
    return d


def figure_quality():
    """One stacked bar: share of raw rows usable for speed, with labelled segments below."""
    h = 1.55 * cm
    d = Drawing(W, h)
    y, bh = 0.72 * cm, 0.5 * cm
    parts = [(usable, "usable for speed", colors.HexColor(BLUE)),
             (no_speed, "real trip, impossible time or distance", colors.HexColor(GREY_BAR)),
             (not_trip, "not a trip (refund, unknown zone, wrong month)", colors.HexColor(ORANGE))]
    x = 0
    for n, _, c in parts:
        w = W * n / total
        d.add(Rect(x, y, w, bh, fillColor=c, strokeColor=colors.white, strokeWidth=0.8))
        x += w
    text(d, 6, y + bh / 2 - 3, f"{usable / total:.1%} usable for speed ({usable / 1e6:.2f} M rows)", 8.6,
         "Times-Bold", colors.white, "start")
    lx = 0
    for n, label, c in parts[1:]:
        d.add(Rect(lx, 0.12 * cm, 7, 7, fillColor=c, strokeColor=None))
        s_ = f"{n / total:.1%}  {label}"
        text(d, lx + 11, 0.12 * cm + 0.5, s_, 8, "Times-Roman", INK, "start")
        lx += 11 + len(s_) * 3.75 + 18
    text(d, W, y + bh + 4, f"{total / 1e6:.2f} million raw rows", 7.8, "Times-Italic", GREY, "end")
    return d


def figure_weather():
    """Calendar strip: each weekday in the comparison window, wet days filled."""
    rows = [("Jan 2024", [(dn, w) for per, dn, w in window_days if per == "before"]),
            ("Jan 2025", [(dn, w) for per, dn, w in window_days if per == "after"])]
    sq, gap, lab = 0.56 * cm, 0.1 * cm, 1.9 * cm
    h = 2 * sq + 0.28 * cm + 0.5 * cm
    d = Drawing(W, h)
    wet_c, dry_c = colors.HexColor("#2f6db3"), colors.HexColor("#e7e7e7")
    for r, (name, cells) in enumerate(rows):
        y = h - (r + 1) * sq - r * 0.28 * cm
        text(d, 0, y + sq / 2 - 3, name, 8.6, "Times-Bold", INK, "start")
        for i, (dn, is_wet) in enumerate(cells):
            x = lab + i * (sq + gap)
            d.add(Rect(x, y, sq, sq, fillColor=wet_c if is_wet else dry_c, strokeColor=None))
            text(d, x + sq / 2, y + sq / 2 - 2.6, str(dn), 7, "Times-Roman", colors.white if is_wet else GREY)
        n_wet = sum(w for _, w in cells)
        text(d, lab + len(cells) * (sq + gap) + 4, y + sq / 2 - 3, f"{n_wet} of {len(cells)} wet", 8.4,
             "Times-Roman", INK, "start")
    ly = 0.08 * cm
    d.add(Rect(lab, ly, 7, 7, fillColor=wet_c, strokeColor=None))
    text(d, lab + 11, ly + 0.5, "wet (1 mm or more of rain or snow)", 7.8, "Times-Roman", INK, "start")
    d.add(Rect(lab + 5.6 * cm, ly, 7, 7, fillColor=dry_c, strokeColor=None))
    text(d, lab + 5.6 * cm + 11, ly + 0.5, "dry", 7.8, "Times-Roman", INK, "start")
    text(d, W, ly + 0.5, "weekdays 5-31 January, holidays excluded", 7.8, "Times-Italic", GREY, "end")
    return d


# ---------------------------------------------------------------- charts
plt.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "DejaVu Serif"],
                     "font.size": 7.5})


def _axes(w_cm, h_cm):
    fig, ax = plt.subplots(figsize=(w_cm / 2.54, h_cm / 2.54), dpi=300)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_linewidth(0.6)
    ax.tick_params(labelsize=7, length=2, width=0.6)
    return fig, ax


def chart_net(w_cm, h_cm):
    bars = [("Raw change, all days", chg("M1"), BLUE, "%"),
            ("Inside zone, dry days", chg("C2"), BLUE, "%"),
            ("Outside zone, dry days", chg("C4"), GREY_BAR, "%"),
            ("Net effect (inside - outside)", net, ORANGE, " pts"),
            ("MTA published figure", chg("C3"), "#8fb3dc", "%")]
    fig, ax = _axes(w_cm, h_cm)
    ys = list(range(len(bars)))[::-1]
    for y, (label, v, c, unit) in zip(ys, bars):
        ax.barh(y, v, height=0.58, color=c, zorder=3)
        ax.text(v + (0.15 if v >= 0 else -0.15), y, f"{v:+.1f}{unit}", va="center",
                ha="left" if v >= 0 else "right", fontsize=7)
    ax.set_yticks(ys, [b[0] for b in bars])
    ax.tick_params(axis="y", length=0)
    ax.axvline(0, color="#444444", lw=0.6)
    ax.set_xlim(-5.2, 6.6)
    ax.set_xlabel("change in median speed (%)", fontsize=7)
    fig.tight_layout(pad=0.3)
    path = FIG / "pdf_net_effect.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def chart_hourly(w_cm, h_cm):
    fig, ax = _axes(w_cm, h_cm)
    ax.axvspan(4.5, 20.5, color="#f0f0f0", zorder=0)
    for period, c, label, dy in (("before", BLUE, "Jan 2024", -13), ("after", ORANGE, "Jan 2025", 9)):
        d = hourly[hourly.period == period]
        ax.plot(d.pickup_hour, d.median_mph, color=c, lw=1.2, zorder=3)
        at = d[d.pickup_hour == 15].iloc[0]
        ax.annotate(label, (15, at.median_mph), xytext=(0, dy), textcoords="offset points", ha="center",
                    fontsize=7, color=c)
    ax.text(12.5, 13.05, "peak toll hours", fontsize=6.6, color="#666666", ha="center")
    ax.set_xticks(range(0, 24, 3), ["0", "3", "6", "9", "12", "15", "18", "21"])
    ax.set_xlim(-0.3, 23.3)
    ax.set_ylim(6.4, 13.6)
    ax.set_xlabel("hour of pickup", fontsize=7)
    ax.set_ylabel("median speed (mph)", fontsize=7)
    fig.tight_layout(pad=0.3)
    path = FIG / "pdf_speed_by_hour.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def figure_charts():
    FIG.mkdir(parents=True, exist_ok=True)
    gutter = 0.5 * cm
    half = (W - gutter) / 2
    h_cm = 4.5
    left = Image(str(chart_net(half / cm, h_cm)), width=half, height=h_cm * cm)
    right = Image(str(chart_hourly(half / cm, h_cm)), width=half, height=h_cm * cm)
    t = Table([[left, right]], colWidths=[half + gutter / 2] * 2)
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return t


# ---------------------------------------------------------------- content
meta = "FDE Data Foundations, Assignment 2 (Track B: NYC TLC trip data)"
if AUTHOR:
    meta = f"<b>{AUTHOR}</b> &nbsp;|&nbsp; " + meta
if REPO_URL:
    meta += f'<br/>Code: <link href="{REPO_URL}" color="#1f3a5f">{REPO_URL}</link>'

story = [P("Did congestion pricing make Manhattan taxis faster?", "title"), Spacer(1, 3), P(meta, "meta"),
         HRFlowable(width="100%", thickness=0.8, color=INK, spaceBefore=7, spaceAfter=2)]

story += [H("1. Problem and stakeholders"),
          P("On 5 January 2025 New York began tolling vehicles entering Manhattan south of 60th Street (the "
            "Congestion Relief Zone) to reduce congestion. Before deciding whether to keep or adjust the toll, the "
            "MTA needs to know whether traffic in the zone actually moves faster. I also considered slow airport "
            "trips and suspicious fares, but airports have no queue data and fare anomalies have no clear decision "
            "behind them; the toll affects the whole city and allows a clean before-and-after test."),
          P("The MTA owns the toll and the KPI; the Taxi and Limousine Commission (TLC) owns the trip data. The KPI "
            "is the <b>median speed of taxi trips that start and end inside the zone, on weekdays between 5am and "
            "9pm</b>, which are the peak toll hours. I compare January 2024 with January 2025.")]

story += [H("2. Data sources and retrieval"),
          P("I split the business question into five smaller questions, each matched to the information it needs "
            "and the system that owns it (Table 1). TLC trip records are the system of record. Retrieval uses files, "
            "JSON APIs and SQL (DuckDB); every download is checked for completeness and kept unchanged."),
          *caption("Table 1. Business questions mapped to the information and sources that answer them", before=1, after=2),
          table([["Question", "Information needed", "Source (owner)", "Grain", "Retrieval and check"],
                 ["Did trips get faster?", "times, distance, zones", "Trip records (TLC)", "one trip",
                  "Parquet; rows = file metadata"],
                 ["Which trips are tolled?", "zone ID, toll boundary", "TLC and MTA zones", "one zone",
                  "CSV and API; 38 of 265 tolled"],
                 ["Was the toll charged?", "fee on each trip", "Trip records (TLC)", "one trip",
                  "Parquet; 2024 fee = unknown"],
                 ["Could weather explain it?", "daily rain and snow", "Open-Meteo", "one day", "API; one record per day"],
                 ["Do official figures agree?", "monthly zone speed", "MTA", "zone, month", "API; count matches API"]],
                [3.6 * cm, 3.25 * cm, 3.1 * cm, 1.9 * cm, W - 11.85 * cm]),
          Spacer(1, 6),
          P("The main gap is that trip records have no GPS trace, so trips that only pass through the zone are "
            "invisible.")]

story += [H("3. Data model and pipeline"),
          P("The model centres on one event, the trip from pickup to dropoff, linked to its zone and its day, with "
            "the toll as the intervention that splits before from after (Figure 1, top). The pipeline (bottom) runs "
            "as one logged command; a month that fails a quality gate is held back, and reruns give the same output."),
          Spacer(1, 4), figure_model_pipeline(),
          *caption("Figure 1. Workflow model (top) and pipeline (bottom).")]

story += [KeepTogether([H("4. Data quality"),
          P("Thirteen business rules flag bad rows instead of deleting them, for example a trip shorter than one "
            "minute, an average speed above 60 mph, or a pickup outside the file's month. Profiling found a $863,380 "
            "fare, a 312,722-mile trip, trips dated 2002, and a vendor whose trips all last zero seconds."),
          Spacer(1, 6), figure_quality()])]

story += [H("5. Results"),
          P(f"On the raw figures every speed measure improved and zone demand grew {chg('M4'):.0f}% (Table 2)."),
          *caption(f"Table 2. Weekdays 5-31 January, excluding public holidays. Speed metrics are based on "
                   f"{n_before:,} trips in 2024 and {n_after:,} in 2025.", before=1, after=2),
          table([["Metric", "Jan 2024", "Jan 2025", "Change"],
                 ["Median speed in the zone, 5am-9pm (KPI)", val("M1", "before") + " mph", val("M1", "after") + " mph", pct("M1")],
                 ["Trips crawling below 5 mph", val("M2", "before", "{:.1f}%"), val("M2", "after", "{:.1f}%"), pct("M2")],
                 ["Slowest 10% of trips (minutes per mile)", val("M3", "before"), val("M3", "after"), pct("M3")],
                 ["Taxi trips in the zone per weekday", val("M4", "before", "{:,.0f}"), val("M4", "after", "{:,.0f}"), pct("M4")],
                 ["Zone trips charged the toll", "not applicable", val("M5", "after", "{:.1f}%"), ""]],
                [7.6 * cm, 2.8 * cm, 2.8 * cm, W - 13.2 * cm])]

story += [H("6. Adjusting for weather"),
          P(f"The raw increase of {chg('M1'):.1f}% cannot be taken at face value: January 2024 was far wetter than "
            "January 2025 (Figure 2), and bad weather slows traffic regardless of any toll. Both periods also start "
            "on 5 January, so the four pre-toll days of 2025 are not counted as \"after\"."),
          Spacer(1, 4), figure_weather(),
          *caption("Figure 2. Wet and dry weekdays in each comparison window."),
          P(f"I therefore compared dry days only, inside the zone against outside it. Dry-day speeds rose "
            f"{chg('C2'):.1f}% inside and fell {abs(chg('C4')):.1f}% outside, a net improvement of about "
            f"<b>{net:.1f} percentage points</b>, in line with the MTA's own {chg('C3'):+.1f}% for taxis and ride-hail "
            "cars in the zone (Figure 3, left). "
            "Gains run from late morning to evening; trips before 6am slowed slightly (right)."),
          Spacer(1, 2), figure_charts(),
          *caption("Figure 3. Left: raw change against the weather-adjusted comparison. Right: median speed by hour "
                   "inside the zone on weekdays.")]

story += [H("7. Facts, assumptions, unknowns and limitations"),
          table([["Type", "Summary"],
                 ["Facts", f"Zone speed {pct('M1')}, crawling trips {pct('M2')}; {net:+.1f} pts vs outside on dry days; "
                  f"toll charged on {kpi.loc['M5', 'after']:.0f}% of zone trips."],
                 ["Assumptions", "Whole-trip speed reflects zone traffic; negative totals are refunds; one weather point "
                  "covers the city."],
                 ["Unknowns", "Pass-through trips (no GPS); why 1.6% of zone trips paid no toll; Uber and Lyft; whether "
                  "the effect lasts."],
                 ["Limitations", "One month per side; 3.2 pts if trace snow counts as wet; control includes airports; "
                  "not proof of cause."]],
                [2.4 * cm, W - 2.4 * cm], bold_first_col=True)]

story += [H("8. Recommendation"),
          P(f"The evidence supports keeping the toll: taxi trips in the zone became about {net:.0f}% faster relative "
            "to the rest of the city, even though more taxi trips were made there. Because the result rests on a "
            "single month, the next step is to rerun the pipeline for February to June. I would also ask the TLC for "
            "a flag showing whether each trip entered the zone, the list of toll exemptions, and a fix for the vendor "
            "that records zero-second trips.")]


def footer(canvas, doc_):
    canvas.saveState()
    canvas.setFont("Times-Roman", 8.5)
    canvas.setFillColor(GREY)
    canvas.drawCentredString(A4[0] / 2, 1.0 * cm, str(doc_.page))
    canvas.restoreState()


doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                        topMargin=1.6 * cm, bottomMargin=1.6 * cm,
                        title="Did congestion pricing make Manhattan taxis faster?",
                        author=AUTHOR or "FDE Assignment 2", subject="FDE Data Foundations Assignment 2")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print("wrote", OUT)
