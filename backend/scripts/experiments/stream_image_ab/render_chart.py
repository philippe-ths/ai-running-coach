"""Render a Garmin-style stacked stream chart from a reconstructed streams_dict.

The image arm of the stream-representation A/B. Charts exactly the four channels
the numeric `stream_view` carries (HR / pace / grade / cadence) plus the elevation
profile (terrain shape, derived from the same altitude signal grade comes from), at
full point resolution, lightly smoothed to the same bucket width the 60-point numeric
view uses so the two arms are noise-matched. Output is PNG bytes sized for Claude
vision (long edge ~1568px).
"""
from __future__ import annotations

import io
from typing import Any, Dict, List, Optional

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from app.services.units.cadence import cadence_doubling_factor  # noqa: E402

_STOPPED_VELOCITY = 0.5  # m/s, mirrors stream_view._velocity_to_pace


def _rolling_mean(a: np.ndarray, window: int) -> np.ndarray:
    """Centered rolling mean that ignores NaN, preserving array length and gaps."""
    if window <= 1:
        return a
    n = len(a)
    out = np.full(n, np.nan)
    half = window // 2
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        seg = a[lo:hi]
        finite = seg[np.isfinite(seg)]
        if finite.size:
            out[i] = finite.mean()
    return out


def _fmt_pace(seconds_per_km: float, _pos=None) -> str:
    if not np.isfinite(seconds_per_km) or seconds_per_km <= 0:
        return ""
    m, s = divmod(int(round(seconds_per_km)), 60)
    return f"{m}:{s:02d}"


def _fmt_clock(minutes: float, _pos=None) -> str:
    total = int(round(minutes * 60))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _robust_range(a: np.ndarray, lo_pct: float, hi_pct: float, pad: float = 0.0):
    finite = a[np.isfinite(a)]
    if not finite.size:
        return None
    lo = float(np.percentile(finite, lo_pct))
    hi = float(np.percentile(finite, hi_pct))
    span = hi - lo
    return lo - pad * span, hi + pad * span


def render_stream_chart(
    streams: Dict[str, List[Any]],
    *,
    title: str = "",
    optimized: bool = False,
) -> Optional[bytes]:
    """streams_dict -> PNG bytes (or None if no time/metric channels).

    optimized=True renders a vision-legibility-tuned chart (#726): a pace axis clamped
    to the running range with dense M:SS gridlines (so reps aren't compressed by walk
    recoveries), a minimum elevation span and fixed grade range (so flat terrain looks
    flat), dense time gridlines (so reps are countable), and larger fonts. Pure
    legibility — no derived values (rep paces, interval structure) are drawn."""
    time = streams.get("time")
    if not time:
        return None
    t = np.asarray(time, dtype=float)
    n = len(t)
    minutes = t / 60.0
    # Bucket width the 60-point numeric view would use, so the image line is
    # smoothed to the same degree the numeric arm is (noise parity, not res loss).
    window = max(1, n // 60)

    def series(key: str) -> Optional[np.ndarray]:
        raw = streams.get(key)
        if not raw or len(raw) != n:
            return None
        return np.asarray(
            [np.nan if v is None else float(v) for v in raw], dtype=float
        )

    hr = series("heartrate")
    vel = series("velocity_smooth")
    grade = series("grade_smooth")
    cad = series("cadence")
    alt = series("altitude")

    # Derived pace (min/km) from velocity, NaN when stopped — mirrors stream_view.
    pace = None
    if vel is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            pace = np.where(vel > _STOPPED_VELOCITY, 1000.0 / vel, np.nan)

    # Cadence -> steps/min (both legs), same doubling stream_view applies.
    if cad is not None:
        finite = cad[np.isfinite(cad) & (cad > 0)]
        mean = float(finite.mean()) if finite.size else None
        cad = cad * cadence_doubling_factor(mean)

    pace_title = "Pace (min/km) — HIGHER = FASTER" if optimized else "Pace (min/km)"
    panels = []
    if hr is not None:
        panels.append(("hr", "Heart rate (bpm)", _rolling_mean(hr, window), "#e6194B", False))
    if pace is not None:
        panels.append(("pace", pace_title, _rolling_mean(pace, window), "#4363d8", True))
    if alt is not None or grade is not None:
        panels.append(("terrain", "Elevation (m) + grade (%)", None, None, False))
    if cad is not None:
        panels.append(("cad", "Cadence (spm)", _rolling_mean(cad, window), "#3cb44b", False))

    if not panels:
        return None

    tfs, lfs = (15, 13) if optimized else (11, 10)  # title / label font sizes
    row_h = 2.9 if optimized else 2.4
    fig, axes = plt.subplots(
        len(panels), 1, figsize=(16, row_h * len(panels)), sharex=True,
        dpi=120 if optimized else 100,
    )
    if len(panels) == 1:
        axes = [axes]
    if title:
        fig.suptitle(title, fontsize=tfs + 2, fontweight="bold", y=0.997)

    for ax, (key, label, data, color, invert) in zip(axes, panels):
        if key == "terrain":
            if alt is not None:
                base = float(np.nanmin(alt))
                ax.fill_between(minutes, _rolling_mean(alt, window), base, color="#9A6324", alpha=0.28, lw=0)
                ax.plot(minutes, _rolling_mean(alt, window), color="#9A6324", lw=1.6, label="elevation")
                ax.set_ylabel("Elevation (m)", fontsize=lfs)
                if optimized:
                    # Minimum span so a flat run reads flat, not mountainous.
                    finite = alt[np.isfinite(alt)]
                    mid = float((finite.min() + finite.max()) / 2)
                    half = max((finite.max() - finite.min()) / 2, 30.0)
                    ax.set_ylim(mid - half, mid + half)
            if grade is not None:
                axg = ax.twinx()
                axg.plot(minutes, _rolling_mean(grade, window), color="#808000", lw=1.0, alpha=0.8, label="grade %")
                axg.axhline(0, color="#808000", lw=0.5, ls=":", alpha=0.5)
                axg.set_ylabel("Grade (%)", color="#808000", fontsize=lfs)
                axg.tick_params(axis="y", labelcolor="#808000", labelsize=lfs - 2)
                if optimized:
                    axg.set_ylim(-15, 15)  # fixed so small grades look small
            ax.set_title(label, fontsize=tfs, loc="left")
        else:
            ax.plot(minutes, data, color=color, lw=1.8 if optimized else 1.6)
            ax.set_title(label, fontsize=tfs, loc="left")
            ax.set_ylabel(label.split(" (")[0], fontsize=lfs)
            if invert:
                ax.invert_yaxis()
                ax.yaxis.set_major_formatter(plt.FuncFormatter(_fmt_pace))
                if optimized and data is not None:
                    # Clamp to the running range so reps aren't compressed by walk
                    # recoveries, and label every 30 s.
                    rng = _robust_range(data, 2, 92, pad=0.05)
                    if rng:
                        ax.set_ylim(rng[1], rng[0])  # inverted: fast(low s) at top
                    ax.yaxis.set_major_locator(plt.MultipleLocator(30))
        if optimized:
            ax.grid(which="major", alpha=0.35, lw=0.8)
            ax.grid(which="minor", alpha=0.15, lw=0.5)
            ax.minorticks_on()
            ax.tick_params(labelsize=lfs - 2)
        else:
            ax.grid(True, alpha=0.25)
        ax.margins(x=0.01)

    axes[-1].set_xlabel("Time (h:mm:ss)", fontsize=lfs)
    axes[-1].xaxis.set_major_formatter(plt.FuncFormatter(_fmt_clock))
    if optimized:
        axes[-1].xaxis.set_major_locator(plt.MultipleLocator(3))   # every 3 min
        axes[-1].xaxis.set_minor_locator(plt.MultipleLocator(1))   # minor every 1 min
    fig.tight_layout(rect=[0, 0, 1, 0.985])

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


if __name__ == "__main__":
    # Smoke: render one activity's chart from the local DB.
    import sys
    from app.db.session import SessionLocal
    from app.models import ActivityStream

    activity_id = sys.argv[1] if len(sys.argv) > 1 else "2c24b603-7dc7-4e80-952e-70b3a23c995e"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "chart.png"
    db = SessionLocal()
    try:
        rows = db.query(ActivityStream).filter(ActivityStream.activity_id == activity_id).all()
        streams = {r.stream_type: r.data for r in rows}
        png = render_stream_chart(streams, title=f"Activity {activity_id[:8]}")
        if png is None:
            print("no chart produced")
            sys.exit(1)
        with open(out_path, "wb") as f:
            f.write(png)
        print(f"wrote {out_path} ({len(png)} bytes)")
    finally:
        db.close()
