"""Draw the two setup diagrams of SETUP.md: arch_lab.png and arch_software.png.

    ~/.hermes/cache/scratch/figs-venv/bin/python -I make_diagrams.py [OUTPUT_DIR]

Same size, font and colours as make_figures.py: 1600x900, blue = computational storage,
orange = disaggregated baseline.
"""

from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, FancyArrowPatch, FancyBboxPatch, Rectangle

CS = "#2F6EBA"
CS_LIGHT = "#DCE8F6"
BASE = "#D9822B"
BASE_LIGHT = "#FBE9D7"
GREY = "#7F7F7F"
LIGHT_GREY = "#EDEDED"
INK = "#222222"


def pick_font():
    for name in ("Calibri", "Carlito"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
            return name
        except ValueError:
            pass
    return "DejaVu Sans"


def canvas():
    plt.rcParams["font.family"] = pick_font()
    figure = plt.figure(figsize=(16, 9), dpi=100)
    axes = figure.add_axes([0, 0, 1, 1])
    axes.set_xlim(0, 160)
    axes.set_ylim(0, 90)
    axes.axis("off")
    return figure, axes


def box(axes, x, y, w, h, face, edge, *, dashed=False, lw=2.0, radius=0.8):
    axes.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle=f"round,pad=0,rounding_size={radius}",
            facecolor=face,
            edgecolor=edge,
            linewidth=lw,
            linestyle="--" if dashed else "-",
        )
    )


def cylinder(axes, cx, y, w, h, face, edge):
    axes.add_patch(Rectangle((cx - w / 2, y), w, h, facecolor=face, edgecolor="none"))
    axes.plot([cx - w / 2, cx - w / 2], [y, y + h], color=edge, lw=1.8)
    axes.plot([cx + w / 2, cx + w / 2], [y, y + h], color=edge, lw=1.8)
    axes.add_patch(Ellipse((cx, y), w, h * 0.35, facecolor=face, edgecolor=edge, lw=1.8))
    axes.add_patch(Ellipse((cx, y + h), w, h * 0.35, facecolor=face, edgecolor=edge, lw=1.8))


def arrow(axes, start, end, colour, *, lw=3.0, style="-|>", scale=22, rad=0.0, dashed=False):
    axes.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle=style,
            mutation_scale=scale,
            color=colour,
            lw=lw,
            linestyle="--" if dashed else "-",
            connectionstyle=f"arc3,rad={rad}",
        )
    )


def lab_panel(axes, left, mode):
    cs = mode == "cs"
    colour = CS if cs else BASE
    light = CS_LIGHT if cs else BASE_LIGHT
    width = 76
    title = "Computational storage (cs)" if cs else "Disaggregated baseline (disagg)"
    axes.text(left + width / 2, 85, title, ha="center", va="center", fontsize=26,
              fontweight="bold", color=colour)

    node_w, gap = 8.2, 1.3
    first = left + (width - (8 * node_w + 7 * gap)) / 2
    router = (left + 14, 40, width - 28, 7)
    router_cx = router[0] + router[2] / 2

    for i in range(8):
        x = first + i * (node_w + gap)
        cx = x + node_w / 2
        # links to the router
        axes.plot([cx, router_cx + (cx - (left + width / 2)) * 0.55], [59, router[1] + router[3]],
                  color=GREY, lw=1.6, zorder=0)
        axes.plot([cx, router_cx + (cx - (left + width / 2)) * 0.55], [29, router[1]],
                  color=GREY, lw=1.6, zorder=0, linestyle="-" if not cs else ":")

        # storage node: OSD + pool, worker on top
        box(axes, x, 59, node_w, 20, "white", INK, lw=1.8)
        axes.text(cx, 76.6, f"n{i}", ha="center", va="center", fontsize=19, color=INK)
        cylinder(axes, cx, 61.5, node_w * 0.62, 5.2, LIGHT_GREY, INK)
        if cs:
            box(axes, x + 0.9, 68.6, node_w - 1.8, 5.2, CS, CS, radius=0.5)
            axes.text(cx, 71.2, "W", ha="center", va="center", fontsize=19, color="white",
                      fontweight="bold")
        else:
            box(axes, x + 0.9, 68.6, node_w - 1.8, 5.2, "white", "#BBBBBB", dashed=True,
                lw=1.4, radius=0.5)

        # compute-only node
        if cs:
            box(axes, x, 14, node_w, 15, "white", "#BBBBBB", dashed=True, lw=1.4)
            axes.text(cx, 25.6, f"c{i}", ha="center", va="center", fontsize=19, color="#AAAAAA")
        else:
            box(axes, x, 14, node_w, 15, "white", INK, lw=1.8)
            axes.text(cx, 25.6, f"c{i}", ha="center", va="center", fontsize=19, color=INK)
            box(axes, x + 0.9, 16, node_w - 1.8, 5.6, BASE, BASE, radius=0.5)
            axes.text(cx, 18.8, "W", ha="center", va="center", fontsize=19, color="white",
                      fontweight="bold")

    box(axes, *router, light, colour, lw=2.4)
    axes.text(router_cx, router[1] + router[3] / 2, "router: every link 400 Mbit/s",
              ha="center", va="center", fontsize=20, color=INK)

    # coordinator tag on n0
    axes.text(first + node_w / 2, 81.2, "coordinator", ha="center", va="center", fontsize=15,
              color=INK, style="italic")

    if cs:
        axes.text(left + width / 2, 53, "data stays on its node", ha="center", va="center",
                  fontsize=21, color=CS, fontweight="bold",
                  bbox=dict(facecolor="white", edgecolor="none", pad=2))
        axes.text(left + width / 2, 33, "compute-only nodes stopped", ha="center",
                  va="center", fontsize=19, color="#999999",
                  bbox=dict(facecolor="white", edgecolor="none", pad=2))
        axes.text(left + width / 2, 7, "8 workers (cs2: 16)", ha="center", va="center",
                  fontsize=21, color=INK)
    else:
        for x0 in (left + 8, left + width - 8):
            arrow(axes, (x0, 58.6), (x0, 47.4), BASE, lw=5, scale=30, style="<|-|>")
            arrow(axes, (x0, 39.6), (x0, 29.4), BASE, lw=5, scale=30, style="<|-|>")
        axes.text(left + width / 2, 53, "inputs and outputs cross the links", ha="center",
                  va="center", fontsize=21, color=BASE, fontweight="bold",
                  bbox=dict(facecolor="white", edgecolor="none", pad=2))
        axes.text(left + width / 2, 7, "8 workers (disagg2: 16)", ha="center", va="center",
                  fontsize=21, color=INK)


def arch_lab(path):
    figure, axes = canvas()
    lab_panel(axes, 2, "cs")
    lab_panel(axes, 82, "disagg")
    axes.plot([80, 80], [4, 88], color="#CCCCCC", lw=1.5)
    # legend
    cylinder(axes, 4.5, 1.2, 3.2, 2.4, LIGHT_GREY, INK)
    axes.text(7.2, 2.4, "OSD + pool", va="center", fontsize=17, color=INK)
    box(axes, 24, 0.8, 3.4, 3.2, CS, CS, radius=0.4)
    axes.text(25.7, 2.4, "W", ha="center", va="center", fontsize=14, color="white",
              fontweight="bold")
    axes.text(28.4, 2.4, "worker", va="center", fontsize=17, color=INK)
    axes.text(98, 2.4, "one Ceph cluster: same data and pools in both modes; n0 also runs mon + mgr",
              ha="center", va="center", fontsize=17, color=GREY)
    figure.savefig(path)
    plt.close(figure)


def label_box(axes, x, y, w, h, title, lines, colour, face, *, title_size=24, size=19):
    box(axes, x, y, w, h, face, colour, lw=2.6)
    axes.text(x + w / 2, y + h - 3.2, title, ha="center", va="center", fontsize=title_size,
              fontweight="bold", color=colour)
    for k, line in enumerate(lines):
        axes.text(x + w / 2, y + h - 8.4 - k * 4.6, line, ha="center", va="center",
                  fontsize=size - 2 if "(" in line else size, color=INK,
                  family="DejaVu Sans Mono" if line.startswith("/") or "(" in line else None)


def arch_software(path):
    figure, axes = canvas()
    # client
    label_box(axes, 2, 50, 34, 30, "Client",
              ["put_dataset", "Graph.map / reduce", "submit(graph, policy)", "job.wait()"],
              INK, "white")

    # coordinator
    box(axes, 42, 36, 58, 48, CS_LIGHT, CS, lw=2.8)
    axes.text(71, 80.2, "Coordinator (in n0)", ha="center", va="center", fontsize=25,
              fontweight="bold", color=CS)
    box(axes, 45, 61, 52, 14, "white", INK, lw=1.8)
    axes.text(71, 71.6, "policy.choose(state)", ha="center", va="center", fontsize=21,
              family="DejaVu Sans Mono", color=INK)
    axes.text(71, 65.0, "Dispatch(task, worker, targets) or wait", ha="center", va="center",
              fontsize=17, color=INK)
    box(axes, 45, 39, 25, 18, "white", INK, lw=1.8)
    axes.text(57.5, 51.8, "Simulation", ha="center", va="center", fontsize=20, color=INK,
              fontweight="bold")
    axes.text(57.5, 46.6, "calibrated rates", ha="center", va="center", fontsize=16, color=INK)
    axes.text(57.5, 42.6, "+ compute model", ha="center", va="center", fontsize=16, color=INK)
    box(axes, 72, 39, 25, 18, "white", INK, lw=1.8)
    axes.text(84.5, 51.8, "Rust planner", ha="center", va="center", fontsize=20, color=INK,
              fontweight="bold")
    axes.text(84.5, 46.6, "beam, MCTS", ha="center", va="center", fontsize=16, color=INK)
    axes.text(84.5, 42.6, "ctypes C ABI v5", ha="center", va="center", fontsize=16, color=INK)
    arrow(axes, (57.5, 57.2), (57.5, 60.8), INK, lw=2, scale=16, style="<|-|>")
    arrow(axes, (84.5, 57.2), (84.5, 60.8), INK, lw=2, scale=16, style="<|-|>")

    arrow(axes, (36.2, 65), (41.8, 65), INK, lw=3)

    # workers: two cards behind, the front one holds the text
    for k in (2, 1):
        box(axes, 112 + 2 * k, 58 + 2 * k, 42, 22, "white", INK, lw=1.8)
    wx, wy = 112, 58
    box(axes, wx, wy, 42, 22, "white", INK, lw=2.2)
    axes.text(wx + 21, wy + 17.6, "Workers (HTTP)", ha="center", va="center", fontsize=23,
              fontweight="bold", color=INK)
    axes.text(wx + 21, wy + 12.0, "/submit  /state  /ack", ha="center", va="center", fontsize=17,
              family="DejaVu Sans Mono", color=INK)
    axes.text(wx + 21, wy + 7.6, "/warm  /recover-publication", ha="center", va="center",
              fontsize=15, family="DejaVu Sans Mono", color=INK)
    axes.text(wx + 21, wy + 2.8, "one task at a time", ha="center", va="center",
              fontsize=16, color=GREY)

    arrow(axes, (100.2, 74), (111.8, 74), CS, lw=3.2)
    axes.text(106, 76.8, "submit", ha="center", va="center", fontsize=16, color=CS)
    arrow(axes, (111.8, 64), (100.2, 64), GREY, lw=3.2)
    axes.text(106, 61.0, "poll, ack", ha="center", va="center", fontsize=16, color=GREY)

    # pools
    axes.text(80, 25.5, "RADOS pools (one per storage node)", ha="center", va="center",
              fontsize=21, color=INK, fontweight="bold")
    for i in range(8):
        cylinder(axes, 30 + i * 14.3, 9, 9, 9, LIGHT_GREY, INK)
        axes.text(30 + i * 14.3, 13.6, f"node{i}", ha="center", va="center", fontsize=16,
                  color=INK)

    # one task's path: read from an input pool, compute, write to the chosen pool
    pool_x = lambda i: 30 + i * 14.3
    arrow(axes, (pool_x(5) + 1.5, 19.8), (118, 57.6), CS, lw=3.4)
    axes.text(111.5, 36.5, "1 read inputs\n(check sha256)", ha="center", va="center",
              fontsize=17, color=CS, bbox=dict(facecolor="white", edgecolor="none", pad=1))
    axes.text(133, 49, "2 compute", ha="center", va="center", fontsize=17, color=INK,
              bbox=dict(facecolor="white", edgecolor=INK, boxstyle="round,pad=0.3"))
    arrow(axes, (148, 57.6), (pool_x(7) + 1.5, 19.8), INK, lw=3.4)
    axes.text(150.5, 36.5, "3 write outputs\nto chosen pool", ha="center", va="center",
              fontsize=17, color=INK, bbox=dict(facecolor="white", edgecolor="none", pad=1))
    axes.text(106, 55.4, "4 publish,\nthen ack", ha="center", va="center", fontsize=15,
              color=GREY)
    figure.savefig(path)
    plt.close(figure)


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    arch_lab(output / "arch_lab.png")
    arch_software(output / "arch_software.png")
    print(output / "arch_lab.png", output / "arch_software.png")


if __name__ == "__main__":
    main()
