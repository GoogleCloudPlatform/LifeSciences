# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Code-generated data charts (matplotlib) for the whitepaper.

Each tool renders a PNG, stores it via the asset store, and returns the
`asset://<id>` token to embed in the whitepaper markdown as
`![Caption](asset://<id>)`. Charts are for REAL data only — the model must pass
figures it sourced (with citations in the surrounding text), never invented.
"""

import io
import textwrap

import matplotlib

matplotlib.use("Agg")  # headless, container-safe
import matplotlib.pyplot as plt

from .assets import save_asset

# House style (matches the PDF's navy/amber palette).
_NAVY = "#0b2545"
_STEEL = "#13315c"
_AMBER = "#f4a261"
_MUTED = "#8a93a1"
_PALETTE = ["#0b2545", "#13315c", "#3e5c88", "#f4a261", "#8a93a1", "#a4243b"]


def _wrap_labels(labels: list[str], width: int = 12) -> list[str]:
    return ["\n".join(textwrap.wrap(str(lbl), width)) or str(lbl) for lbl in labels]


def _set_x_category_labels(ax, labels: list[str]) -> None:
    """Wrap long x-axis category labels and rotate when they would collide."""
    wrapped = _wrap_labels(labels)
    ax.set_xticks(range(len(wrapped)))
    longest = max((len(line) for lbl in wrapped for line in lbl.split("\n")), default=0)
    # Rough width budget: ~6.4in plot fits ~90 chars of 8pt text per row.
    if len(wrapped) * (longest + 2) > 90 or len(wrapped) > 7:
        ax.set_xticklabels(wrapped, rotation=30, ha="right", fontsize=7.5)
    else:
        ax.set_xticklabels(wrapped, fontsize=8.5)


def _finish(fig, asset_id: str) -> str:
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return save_asset(asset_id, buf.getvalue())


def bar_chart(
    asset_id: str,
    title: str,
    categories: list[str],
    values: list[float],
    y_label: str = "",
) -> dict:
    """Render a vertical bar chart PNG (e.g. burn by quarter, R&D vs G&A, ORR by
    cohort) and store it as an embeddable asset.

    Args:
        asset_id: Short unique id for this chart, e.g. "chart_burn".
        title: Chart title.
        categories: X-axis category labels.
        values: Numeric value per category (same length as categories).
        y_label: Optional y-axis label (e.g. "$M", "ORR %").

    Returns:
        dict {status, token} where token is "asset://<id>" to embed with
        `![caption](token)` in the whitepaper markdown.
    """
    if len(categories) != len(values):
        return {"status": "error", "message": "categories and values length mismatch"}
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    bars = ax.bar(range(len(values)), values, color=_NAVY)
    _set_x_category_labels(ax, categories)
    if len(values) >= 1:
        bars[-1].set_color(_AMBER)
    ax.set_title(title, color=_NAVY, fontsize=12, fontweight="bold")
    ax.set_ylabel(y_label, color=_STEEL)
    ax.tick_params(colors=_STEEL)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.grid(axis="y", color="#e3e7ee", linewidth=0.7)
    ax.set_axisbelow(True)
    return {"status": "success", "token": _finish(fig, asset_id)}


def line_chart(
    asset_id: str,
    title: str,
    x_labels: list[str],
    series: dict[str, list[float]],
    y_label: str = "",
) -> dict:
    """Render a multi-series line chart PNG (e.g. cash balance and burn over
    time, enrollment trajectory) and store it as an embeddable asset.

    Args:
        asset_id: Short unique id, e.g. "chart_cash_trend".
        title: Chart title.
        x_labels: Shared x-axis labels (e.g. ["Q1'25","Q2'25",...]).
        series: Mapping of series name -> list of values aligned to x_labels.
        y_label: Optional y-axis label.

    Returns:
        dict {status, token}.
    """
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    for i, (name, vals) in enumerate(series.items()):
        ax.plot(
            range(len(vals)),
            vals,
            marker="o",
            linewidth=2,
            color=_PALETTE[i % len(_PALETTE)],
            label=name,
        )
    _set_x_category_labels(ax, x_labels)
    ax.set_title(title, color=_NAVY, fontsize=12, fontweight="bold")
    ax.set_ylabel(y_label, color=_STEEL)
    ax.tick_params(colors=_STEEL)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.grid(color="#e3e7ee", linewidth=0.7)
    ax.set_axisbelow(True)
    if len(series) > 1:
        ax.legend(frameon=False, fontsize=8)
    return {"status": "success", "token": _finish(fig, asset_id)}


def horizontal_bar_chart(
    asset_id: str,
    title: str,
    labels: list[str],
    values: list[float],
    x_label: str = "",
) -> dict:
    """Render a horizontal bar chart PNG, ideal for ranked comparisons (e.g.
    peak-sales by indication, competitor ORR ranking, TAM by segment).

    Args:
        asset_id: Short unique id, e.g. "chart_peaksales".
        title: Chart title.
        labels: Category label per bar.
        values: Numeric value per bar (same length as labels).
        x_label: Optional x-axis label.

    Returns:
        dict {status, token}.
    """
    if len(labels) != len(values):
        return {"status": "error", "message": "labels and values length mismatch"}
    order = sorted(range(len(values)), key=lambda i: values[i])
    labels = _wrap_labels([labels[i] for i in order], width=24)
    values = [values[i] for i in order]
    fig, ax = plt.subplots(figsize=(6.4, max(2.4, 0.5 * len(labels) + 1)))
    ax.barh(labels, values, color=_STEEL)
    ax.tick_params(axis="y", labelsize=8.5)
    ax.set_title(title, color=_NAVY, fontsize=12, fontweight="bold")
    ax.set_xlabel(x_label, color=_STEEL)
    ax.tick_params(colors=_STEEL)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.grid(axis="x", color="#e3e7ee", linewidth=0.7)
    ax.set_axisbelow(True)
    return {"status": "success", "token": _finish(fig, asset_id)}
