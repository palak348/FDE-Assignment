"""Stage 6 - publish outputs (atomic writes: *.tmp then rename)."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from . import config as C  # noqa: E402
from .log import get_logger  # noqa: E402

log = get_logger()

BEFORE_COLOR, AFTER_COLOR = "#2a78d6", "#eb6834"
INK, INK_MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#ffffff"


def _atomic(path: Path, write) -> None:
    tmp = path.with_name(path.name + ".tmp")
    write(tmp)
    tmp.replace(path)


def _csv(df: pd.DataFrame, name: str) -> None:
    _atomic(C.OUTPUT_DIR / name, lambda p: df.to_csv(p, index=False, float_format="%.4f"))


def _fmt(v, unit):
    if pd.isna(v):
        return "n/a"
    return f"{v:,.0f}" if unit == "trips/day" else f"{v:,.2f}"


def _chart(hourly: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.axvspan(C.PEAK_START_HOUR - 0.5, C.PEAK_END_HOUR - 0.5, color="#f0efec", zorder=0)
    ax.text(C.PEAK_START_HOUR - 0.3, 0.02, "toll peak window", transform=ax.get_xaxis_transform(),
            color=INK_MUTED, fontsize=8)
    # Direct labels sit at the afternoon peak where the gap is widest; before above, after below.
    for period, color, label, dy in (("before", BEFORE_COLOR, "Jan 2024 (before)", -18),
                                     ("after", AFTER_COLOR, "Jan 2025 (after)", 10)):
        d = hourly[hourly.period == period]
        ax.plot(d.pickup_hour, d.median_mph, color=color, lw=2, marker="o", ms=4, label=label, zorder=3)
        at = d[d.pickup_hour == 16].iloc[0]
        ax.annotate(label, (at.pickup_hour, at.median_mph), xytext=(0, dy), textcoords="offset points",
                    ha="center", va="center", fontsize=8, color=INK)
    ax.set_xlim(-0.5, 23.5)
    ax.set_xticks(range(0, 24, 3), [f"{h:02d}:00" for h in range(0, 24, 3)])
    ax.set_ylabel("median speed (mph)", color=INK_MUTED)
    ax.set_title("Taxi trips inside the Congestion Relief Zone, weekdays: median speed by pickup hour",
                 loc="left", fontsize=10, color=INK)
    ax.grid(axis="y", color=GRID, lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=8)
    ax.legend(frameon=False, fontsize=8, loc="upper right", bbox_to_anchor=(0.86, 1))
    fig.tight_layout()
    _atomic(C.OUTPUT_DIR / "cbd_speed_by_hour.png", lambda p: fig.savefig(p, format="png"))
    plt.close(fig)


def _style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=8)


def _net_effect_chart(table: pd.DataFrame) -> None:
    """The judgement call as one picture: raw change -> weather-fair -> control -> net."""
    from .metrics import net_effect_pp
    t = table.set_index("metric_id")["change_pct"]
    bars = [("Inside zone, all days (raw KPI)", t["M1"], BEFORE_COLOR),
            ("Inside zone, dry days only", t["C2"], BEFORE_COLOR),
            ("Outside zone, dry days (control)", t["C4"], "#8a8984"),
            ("Net effect = inside - outside", net_effect_pp(table), AFTER_COLOR),
            ("Cross-check: MTA published", t["C3"], "#86b6ef")]
    fig, ax = plt.subplots(figsize=(8, 3.4), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    _style(ax)
    ys = list(range(len(bars)))[::-1]
    for y, (label, v, color) in zip(ys, bars):
        ax.barh(y, v, height=0.62, color=color, zorder=3)
        ax.text(v + (0.12 if v >= 0 else -0.12), y, f"{v:+.1f}{' pts' if 'Net' in label else '%'}",
                va="center", ha="left" if v >= 0 else "right", fontsize=9, color=INK,
                fontweight="bold" if "Net" in label else "normal")
    ax.set_yticks(ys, [b[0] for b in bars])
    ax.axvline(0, color=INK_MUTED, lw=0.8, zorder=4)
    ax.set_xlim(min(-4.6, min(b[1] for b in bars) - 1.2), max(b[1] for b in bars) + 1.4)
    ax.set_xlabel("change in median weekday-peak speed, Jan 2025 vs Jan 2024", color=INK_MUTED, fontsize=8)
    ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
    ax.tick_params(axis="y", length=0, labelsize=8.5, labelcolor=INK)
    ax.set_title("Raw +1.9% mixes toll and weather; the fair comparison says +3.8 pts", loc="left",
                 fontsize=10, color=INK)
    fig.tight_layout()
    _atomic(C.OUTPUT_DIR / "net_effect.png", lambda p: fig.savefig(p, format="png"))
    plt.close(fig)


SEVERITY_COLORS = {"exclude_all": "#d03b3b", "exclude_speed": "#ec835a", "warn": "#fab219"}
SEVERITY_LABELS = {"exclude_all": "excluded from all metrics", "exclude_speed": "excluded from speed metrics",
                   "warn": "warning only (kept)"}


def _dq_chart(dq: pd.DataFrame, month: str) -> None:
    d = dq[(dq.source_month == month) & (dq.rows_flagged > 0)].copy()
    order = {"exclude_all": 0, "exclude_speed": 1, "warn": 2}
    d = d.sort_values(["severity", "rows_flagged"], key=lambda s: s.map(order) if s.name == "severity" else s,
                      ascending=[True, False])
    fig, ax = plt.subplots(figsize=(8, 3.3), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    _style(ax)
    ys = list(range(len(d)))[::-1]
    for y, r in zip(ys, d.itertuples()):
        ax.barh(y, r.share * 100, height=0.62, color=SEVERITY_COLORS[r.severity], zorder=3)
        ax.text(r.share * 100 + 0.2, y, f"{r.rows_flagged:,}  ({r.share:.2%})", va="center", fontsize=7.5, color=INK)
    ax.set_yticks(ys, [r.rule.replace("_", " ") for r in d.itertuples()])
    ax.tick_params(axis="y", length=0, labelsize=8, labelcolor=INK)
    ax.set_xlim(0, d.share.max() * 100 * 1.35)
    ax.set_xlabel(f"% of {month} rows flagged (rows are flagged, never deleted)", color=INK_MUTED, fontsize=8)
    ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=c, label=SEVERITY_LABELS[s]) for s, c in SEVERITY_COLORS.items()],
              frameon=False, fontsize=7.5, loc="lower right")
    ax.set_title(f"Data-quality rules: what they caught in {month}", loc="left", fontsize=10, color=INK)
    fig.tight_layout()
    _atomic(C.OUTPUT_DIR / "dq_flags.png", lambda p: fig.savefig(p, format="png"))
    plt.close(fig)


def write_outputs(con, run_id, months, table, rows, context, hourly) -> None:
    C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    in_list = ", ".join(f"'{m}'" for m in months)

    _csv(table, "kpi_before_after.csv")
    _csv(pd.DataFrame(rows), "metrics_by_month.csv")
    _csv(hourly, "cbd_speed_by_hour.csv")
    dq = con.execute(f"select * from dq_results where source_month in ({in_list}) "
                     "order by source_month, rule_id").df()
    _csv(dq, "dq_rule_results.csv")
    gates = con.execute("select source_month, gate, passed, detail from gate_results where run_id = ? "
                        "order by source_month, gate", [run_id]).df()
    _csv(gates, "gate_results.csv")
    _chart(hourly)
    _net_effect_chart(table)
    _dq_chart(dq, max(months))

    # ---- evidence.md: the one-page table a stakeholder reads.
    from .metrics import NET_EFFECT, net_effect_pp
    t = table.set_index("metric_id")
    net = net_effect_pp(table)
    lines = [f"# Evidence table (generated by run `{run_id}`)", "",
             "Window: weekdays from the 5th to the end of January, federal holidays excluded. "
             "Peak = 05:00-21:00. Only valid, speed-eligible trips feed speed metrics.", "",
             "## Headline", "",
             "| Question | Answer |", "|---|---|",
             f"| Raw change in CBD peak speed (M1) | {t.loc['M1', 'change_pct']:+.1f}% |",
             f"| Same, dry days only (C2) | {t.loc['C2', 'change_pct']:+.1f}% |",
             f"| Outside the CBD, dry days only (C4) | {t.loc['C4', 'change_pct']:+.1f}% |",
             f"| **Net effect attributable to the zone ({NET_EFFECT[0]} - {NET_EFFECT[1]})** | "
             f"**{net:+.1f} percentage points** |", "",
             "## Metrics", "",
             "| ID | Metric | Unit | Jan 2024 (before) | Jan 2025 (after) | Change | Sample: trips, M4 = days (before / after) |",
             "|---|---|---|---:|---:|---:|---:|"]
    for r in table.itertuples():
        chg = "n/a" if pd.isna(r.change_pct) else f"{r.change_pct:+.1f}%"
        if r.direction:
            chg += f" ({r.direction})"
        n = "" if pd.isna(r.n_before) else f"{int(r.n_before):,} / {int(r.n_after):,}"
        lines.append(f"| {r.metric_id} | {r.metric} | {r.unit} | {_fmt(r.before, r.unit)} | "
                     f"{_fmt(r.after, r.unit)} | {chg} | {n} |")

    ctx = {r.period: r for r in context.itertuples()}
    lines += ["", f"Window days: before {ctx['before'].window_days} ({ctx['before'].wet_days} wet), "
                  f"after {ctx['after'].window_days} ({ctx['after'].wet_days} wet).", "",
              "## Batch gates", "", "| Month | Gate | Result | Detail |", "|---|---|---|---|"]
    lines += [f"| {g.source_month} | {g.gate} | {'PASS' if g.passed else 'FAIL'} | {g.detail} |"
              for g in gates.itertuples()]

    piv = dq.pivot_table(index=["rule_id", "rule", "severity"], columns="source_month",
                         values="rows_flagged", aggfunc="first").reset_index()
    lines += ["", "## Row rules (rows flagged, never deleted)", "",
              "| Rule | Name | Severity | " + " | ".join(months) + " |",
              "|---|---|---|" + "---:|" * len(months)]
    totals = dict(con.execute(f"select source_month, count(*) from fact_trip where source_month in ({in_list}) "
                              "group by 1").fetchall())
    for r in piv.to_dict("records"):
        cells = [f"{int(r[m]):,} ({r[m] / totals[m]:.2%})" for m in months]
        lines.append(f"| {r['rule_id']} | {r['rule']} | {r['severity']} | " + " | ".join(cells) + " |")

    _atomic(C.OUTPUT_DIR / "evidence.md", lambda p: p.write_text("\n".join(lines) + "\n", encoding="utf-8"))
    log.info("report: outputs written to %s", C.OUTPUT_DIR)
