"""Candidate figures for Aakash's Demo 3 deck, as native PowerPoint shapes and charts.

Reads the CSVs in ../demo-data and writes figures.pptx: one candidate figure per slide, in the
style of his Demo 1 deck (computational-storage-v5.pptx): 13.333 x 7.5 in, IBM Plex, accent blue.
The title and subtitle areas are left empty for his words; the data source and the plotted numbers
are in the speaker notes. Colours, labels, sizes and layouts are the named constants below.

    PY=~/.hermes/environments/documents/gen-038f124f32b645da97305a6570c22df3/venv/bin/python
    $PY -I task_stages.py && $PY -I skew_shards.py      # refresh t8 and t9 from the run records
    $PY -I droid_objects.py                             # refresh t10 from the run records
    $PY -I make_slide_figures.py [OUTPUT.pptx]
    $PY ~/.hermes/skills/productivity/microsoft-office/scripts/pptx_render.py --outdir render --dpi 100 figures.pptx
    (cd render && montage -label '%t' slide-*.png -tile 4x -geometry 667x375+10+10 -border 1 \\
        -bordercolor '#C7D1DC' -pointsize 20 -background white contact.png)
"""

import csv
import statistics
import sys
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.chart.datalabel import DataLabels
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_MARKER_STYLE, XL_TICK_LABEL_POSITION
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml import parse_xml
from pptx.oxml.ns import nsdecls, qn
from pptx.util import Inches, Pt

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "demo-data"
CEPHDAG = HERE.parents[2] / "cephdag"

SANS = "IBM Plex Sans"
MEDIUM = "IBM Plex Sans Medium"
MONO = "IBM Plex Mono"
SLIDE_W, SLIDE_H = 40 / 3, 7.5
FIGURE_X, FIGURE_Y, FIGURE_W, FIGURE_H = 0.67, 1.85, 12.0, 5.15

INK = "171B21"
SECONDARY = "586574"
CS = "0F62FE"
CS_LIGHT = "9DBCFF"
FOCUS = CS
FOCUS_LIGHT = CS_LIGHT
BASELINE = "9C6510"
BASELINE_LIGHT = "D9B98A"
BASELINE_TINT = "FBF0E0"
OTHER_BAR = "C7D1DC"
OTHER_BAR_LIGHT = "E6EBF0"
OTHER_LINE = SECONDARY
NAVY = "2C5F8D"
REFERENCE = SECONDARY
GRIDLINE = "EEF1F4"
AXIS_LINE = "C7D1DC"
CS_TINT = "EAF1FF"
PANEL = "F3F5F8"
PANEL_LINE = "C7D1DC"
CLIENT_FILL = "E8F0F7"
NODE_FILL = "FCFDFE"
NODE_LINE = "B4BFCA"
STORE_FILL = "EEF1F4"
STORE_LINE = "4A5866"
CONNECTOR = "4A5866"
LABEL_COLOURS = {OTHER_BAR: SECONDARY, OTHER_BAR_LIGHT: SECONDARY}
FADED_ALPHA = 30000

NODE_LINE_PT = 1.15
BOX_LINE_PT = 1.2
CONNECTOR_PT = 1.5
CONTAINER_RADIUS = 0.04545
BOX_RADIUS = 0.09859

TICK_PT = 16
AXIS_TITLE_PT = 18
LABEL_PT = 18
NODE_PT = 18
INNER_PT = 16
SMALL_PT = 14
CODE_PT = 20
BIG_PT = 53
BIG_LABEL_PT = 20
CAPS_PT = 16

ITERATIONS_TITLE = "kernel iterations (log scale)"
ITERATIONS_CATEGORY_TITLE = "kernel iterations"
SPEEDUP_TITLE = "speedup over separate"
SECONDS_TITLE = "wall time (s)"
LINK_SPEED_TITLE = "link speed (Mbit/s, log scale)"
REFERENCE_LABEL = "1×"

LINK_LABEL = "400 Mbit/s"
STORAGE_LABEL = "storage"
WORKER_LABEL = "worker"
ROUTER_LABEL = "router"
CS_LABEL = "combined"
BASELINE_LABEL = "separate"

POLICY_LABELS = {
    "native-mcts-heuristic-128": "MCTS-128",
    "fifo-hash": BASELINE_LABEL,
}


def read_csv(name):
    with open(DATA / name, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def numbers(text):
    return [float(part) for part in text.split(",")]


def rgb(hex_colour):
    return RGBColor.from_string(hex_colour)


def label_for(policy):
    return POLICY_LABELS.get(policy, policy)


def short(value):
    return f"{value:,g}"


def set_font(font, size, colour=INK, name=SANS):
    font.name = name
    font.size = Pt(size)
    font.bold = False
    font.color.rgb = rgb(colour)


def set_alpha(colour_parent, alpha):
    srgb = colour_parent.find(".//" + qn("a:srgbClr"))
    alpha_element = etree.SubElement(srgb, qn("a:alpha"))
    alpha_element.set("val", str(alpha))


def drop_theme_style(shape):
    style = shape._element.find(qn("p:style"))
    if style is not None:
        shape._element.remove(style)


def add_text(slide, text, x, y, w, h, size, colour=INK, name=SANS, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = anchor
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    run = paragraph.add_run()
    run.text = text
    set_font(run.font, size, colour, name)
    return box


def add_shape(slide, kind, x, y, w, h, fill, line, line_pt, radius=None, faded=False):
    shape = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if radius is not None:
        shape.adjustments[0] = radius
    drop_theme_style(shape)
    if fill is None:
        shape.fill.background()
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = rgb(fill)
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = rgb(line)
        shape.line.width = Pt(line_pt)
    if faded:
        if fill is not None:
            set_alpha(shape._element.spPr.find(qn("a:solidFill")), FADED_ALPHA)
        if line is not None:
            set_alpha(shape._element.spPr.find(qn("a:ln")), FADED_ALPHA)
    return shape


def label_shape(shape, text, size=NODE_PT, colour=INK, name=MEDIUM, faded=False, anchor=MSO_ANCHOR.MIDDLE):
    frame = shape.text_frame
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = anchor
    frame.word_wrap = False
    paragraph = frame.paragraphs[0]
    paragraph.alignment = PP_ALIGN.CENTER
    run = paragraph.add_run()
    run.text = text
    set_font(run.font, size, colour, name)
    if faded:
        set_alpha(run.font._rPr, FADED_ALPHA)


def add_box(slide, text, x, y, w, h, fill, line, size=NODE_PT, faded=False):
    shape = add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, fill, line, BOX_LINE_PT, BOX_RADIUS, faded)
    label_shape(shape, text, size, faded=faded)
    return shape


def add_store(slide, text, x, y, w, h, size=NODE_PT, faded=False):
    shape = add_shape(slide, MSO_SHAPE.CAN, x, y, w, h, STORE_FILL, STORE_LINE, BOX_LINE_PT, BOX_RADIUS, faded)
    label_shape(shape, text, size, faded=faded)
    shape.text_frame.margin_top = Inches(h * 0.2)
    return shape


def add_container(slide, x, y, w, h, faded=False):
    return add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, NODE_FILL, NODE_LINE, NODE_LINE_PT,
                     CONTAINER_RADIUS, faded)


def add_line(slide, x1, y1, x2, y2, colour, width_pt=CONNECTOR_PT, arrow=False, both=False, dash=None, faded=False):
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    drop_theme_style(line)
    line.line.color.rgb = rgb(colour)
    line.line.width = Pt(width_pt)
    if dash is not None:
        line.line.dash_style = dash
    ln = line._element.spPr.find(qn("a:ln"))
    if faded:
        set_alpha(ln, FADED_ALPHA)
    if both:
        etree.SubElement(ln, qn("a:headEnd")).set("type", "triangle")
    if arrow or both:
        etree.SubElement(ln, qn("a:tailEnd")).set("type", "triangle")
    return line


def add_path(slide, points, colour, width_pt=CONNECTOR_PT, arrow=True, dash=None):
    """An orthogonal connector as straight segments; the arrowhead is on the last one."""
    for index, ((x1, y1), (x2, y2)) in enumerate(zip(points, points[1:])):
        last = index == len(points) - 2
        add_line(slide, x1, y1, x2, y2, colour, width_pt, arrow=arrow and last, dash=dash)


NOTES_PLACEHOLDERS = (
    '<p:sp %s><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr>'
    '<a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg"/></p:nvPr>'
    '</p:nvSpPr><p:spPr/></p:sp>',
    '<p:sp %s><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/>'
    '</p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/>'
    '<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>',
)


def blank_slide(presentation, notes):
    layouts = presentation.slide_layouts
    slide = presentation.slides.add_slide(layouts.get_by_name("Blank", layouts[0]))
    notes_slide = slide.notes_slide
    if notes_slide.notes_text_frame is None:
        # His Demo 1 deck has a notes master without placeholders, so a new notes slide has none.
        for placeholder in NOTES_PLACEHOLDERS:
            notes_slide.shapes._spTree.append(parse_xml(placeholder % nsdecls("p", "a")))
    notes_slide.notes_text_frame.text = notes
    return slide


# Where a task's time goes ------------------------------------------------------------------

STAGE_COLOURS = {
    "disagg2": {"read": BASELINE, "compute": INK, "write": BASELINE_LIGHT},
    "cs2": {"read": CS, "compute": INK, "write": CS_LIGHT},
}
STAGE_MODES = {"disagg2": BASELINE_LABEL, "cs2": CS_LABEL}
STAGE_NAME_COLOURS = {"read": "FFFFFF", "compute": "FFFFFF", "write": INK}
STAGES_LAYOUT = (0.13, 0.04, 0.80, 0.60)
STAGES_X_MAX = 4.5
STAGES_TOTAL_SPACE = 0.7
STAGES_TITLE = "median seconds per task"
STAGES_GROUP_LABELS = {1: "1 ITERATION", 256: "256 ITERATIONS"}
STAGES_NAMED_ROW = (256, "disagg2")


def stage_chart(slide, rows, iterations, y, h, show_axis):
    stages = ("read", "compute", "write")
    modes = list(STAGE_MODES)
    medians = {(row["mode"], row["stage"]): float(row["median s"]) for row in rows
               if int(row["iterations"]) == iterations}
    totals = [round(sum(medians[(mode, stage)] for stage in stages), 2) for mode in modes]
    data = CategoryChartData()
    data.categories = [STAGE_MODES[mode] for mode in reversed(modes)]
    for stage in stages:
        data.add_series(stage, [medians[(mode, stage)] for mode in reversed(modes)])
    data.add_series("total", [STAGES_TOTAL_SPACE for _ in modes])
    chart = add_chart(slide, XL_CHART_TYPE.BAR_STACKED, data, y=y, h=h)
    remove_auto_title(chart)
    layout = STAGES_LAYOUT if show_axis else (STAGES_LAYOUT[0], 0.05, STAGES_LAYOUT[2], 0.90)
    set_plot_layout(chart, *layout)
    plot = chart.plots[0]
    plot.gap_width = 45
    plot.overlap = 100
    plot.vary_by_categories = False
    style_axis(chart.category_axis, None)
    x_axis = chart.value_axis
    style_axis(x_axis, STAGES_TITLE if show_axis else None, gridlines=True)
    x_axis.minimum_scale = 0
    x_axis.maximum_scale = STAGES_X_MAX
    x_axis.major_unit = 1
    x_axis.tick_labels.number_format = "0"
    x_axis.tick_labels.number_format_is_linked = False
    if not show_axis:
        x_axis.tick_label_position = XL_TICK_LABEL_POSITION.NONE
    for series, stage in zip(plot.series, stages):
        for index, mode in enumerate(reversed(modes)):
            colour_bar(series.points[index], STAGE_COLOURS[mode][stage])
    total_series = plot.series[-1]
    for index, mode in enumerate(reversed(modes)):
        total_series.points[index].format.fill.background()
        total_series.points[index].format.line.fill.background()
        colour = STAGE_COLOURS[mode]["read"]
        custom_point_label(total_series.points[index], f" {totals[modes.index(mode)]:.2f}",
                           XL_LABEL_POSITION.INSIDE_BASE, colour)
    if iterations == STAGES_NAMED_ROW[0]:
        name_segments(slide, PlotBox(FIGURE_X, y, FIGURE_W, h, layout, 0, 1), modes, medians, stages)
    return totals


def name_segments(slide, box, modes, medians, stages):
    """Series names as text on the segments of one bar, so they never wrap inside a data label."""
    bar_index = list(reversed(modes)).index(STAGES_NAMED_ROW[1])
    bar_middle_y = box.y + box.h * (1 - (bar_index + 0.5) / len(modes))
    start = 0
    for stage in stages:
        seconds = medians[(STAGES_NAMED_ROW[1], stage)]
        middle_x = box.x + (start + seconds / 2) / STAGES_X_MAX * box.w
        add_text(slide, stage, middle_x - 0.8, bar_middle_y - 0.2, 1.6, 0.4, LABEL_PT, STAGE_NAME_COLOURS[stage],
                 MEDIUM)
        start += seconds


def task_stages(presentation):
    rows = read_csv("t8_task_stages.csv")
    lines = []
    for row in rows:
        lines.append(f"{row['mode']} {row['iterations']} it {row['stage']}: median {row['median s']} s, "
                     f"sum {row['sum s']} s, {float(row['share of task time']):.1%} of summed task time, "
                     f"{row['tasks']} tasks")
    notes = (
        "Source: demo3/demo-data/t8_task_stages.csv, written by demo3/slides/task_stages.py from the per-run "
        "records ~/datasets/cephdag-demo3/campaigns/demo3-slots/independent-i{1,256}-r*-{cs2,disagg2}/a1/"
        "run-*.json, field tasks[*].result.stages (kind read/compute/write, seconds). Bars: median seconds of "
        "each stage over all tasks of all runs of that mode and intensity (separate = disagg2 fifo-hash: 2 runs x "
        "256 tasks; combined = cs2: data-local and heft runs, 4 runs x 256 tasks). The bar length "
        "is the sum of the three medians, written at the end of each bar. Colours: brown/light brown = separate "
        "read/write, blue/light blue = combined read/write, black = compute. Plotted: "
        + "; ".join(lines) + ".")
    slide = blank_slide(presentation, notes)
    add_text(slide, STAGES_GROUP_LABELS[1], FIGURE_X, 1.85, 4.0, 0.3, SMALL_PT, SECONDARY, MONO, PP_ALIGN.LEFT)
    stage_chart(slide, rows, 1, 2.15, 1.85, show_axis=False)
    add_text(slide, STAGES_GROUP_LABELS[256], FIGURE_X, 4.1, 4.0, 0.3, SMALL_PT, SECONDARY, MONO, PP_ALIGN.LEFT)
    stage_chart(slide, rows, 256, 4.4, 2.6, show_axis=True)
    return slide


# The client interface as code --------------------------------------------------------------

QUICKSTART_REF = "cephdag/examples/quickstart.py"
QUICKSTART_LINES = (
    'client = Client(Cluster.load(), models="results/models.json")',
    "data = client.dataset(name)",
    "graph = Graph()",
    'mapped = graph.map("map@1", data, iterations=32, ratios=[0.5])',
    'graph.reduce("join@1", mapped, iterations=1, ratios=[0.5])',
    'job = client.submit(graph, "heft")',
    "job.wait(timeout=600)",
    "for ref in job.outputs():",
    '    print(ref.pool, ref.key, len(client.read(ref)), "bytes")',
)
SCHEDULER_ARGUMENT = '"heft"'
CODE_PANEL = (FIGURE_X, 2.0, FIGURE_W, 4.75)
CODE_INSET = 0.6


def check_quickstart_lines():
    """Every kept line is verbatim in the repository's quickstart, when the repository is here."""
    if not CEPHDAG.exists():
        return "not checked (no cephdag repository)"
    source = (CEPHDAG / "examples/quickstart.py").read_text().splitlines()
    stripped = [line.strip() for line in source]
    for line in QUICKSTART_LINES:
        if line.strip() not in stripped:
            raise ValueError(f"not in {QUICKSTART_REF}: {line}")
    return "checked verbatim against the repository"


def client_code(presentation):
    check = check_quickstart_lines()
    notes = (
        f"Source: {QUICKSTART_REF}; {check}. Kept, in order, from main(), each with the indent of main() removed (the dataset line also loses the try block around it): "
        + " | ".join(QUICKSTART_LINES)
        + ". Dropped: the docstring and imports, name = \"quickstart\", the try/except that creates the dataset "
        "with put_dataset, the metrics lines (job.metrics() and its print), and the __main__ guard. Blue: the "
        "scheduler name passed to submit(graph, scheduler); any policy name (heft-cc, lp-plan2, data-local, ...) "
        "or an object with choose() goes there.")
    slide = blank_slide(presentation, notes)
    x, y, w, h = CODE_PANEL
    add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, PANEL, PANEL_LINE, BOX_LINE_PT, CONTAINER_RADIUS)
    box = slide.shapes.add_textbox(Inches(x + CODE_INSET), Inches(y), Inches(w - 2 * CODE_INSET), Inches(h))
    frame = box.text_frame
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    frame.word_wrap = False
    for index, line in enumerate(QUICKSTART_LINES):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = PP_ALIGN.LEFT
        paragraph.line_spacing = 1.2
        before, found, after = line.partition(SCHEDULER_ARGUMENT)
        for text, colour in ((before, INK), (found, CS), (after, INK)):
            if text:
                run = paragraph.add_run()
                run.text = text
                set_font(run.font, CODE_PT, colour, MONO)
    return slide


# Software architecture ---------------------------------------------------------------------

ARCH_NODES = ("node 0", "node 1", "node 7")
ARCH_NODE_X, ARCH_NODE_W, ARCH_NODE_H = 8.35, 4.32, 1.42
ARCH_NODE_YS = (1.9, 3.62, 5.58)
ARCH_GAP_Y = 5.12
CLIENT_BOX = (FIGURE_X, 3.75, 1.95, 1.0)
COORDINATOR_BOX = (3.3, 2.55, 4.15, 3.4)


def software_architecture(presentation):
    notes = (
        "Figure only; no data plotted. Source: demo3/demo-data/SETUP.md sections 3-4 and arch_software.png. "
        "Client (Cluster.load, Client, Graph, submit) -> coordinator, which runs on node 0 and at every event "
        "(a completion or a status change) rebuilds a simulation of the cluster and asks the policy which ready "
        "task to start on which free worker and where to write its outputs, or to wait; the cost model "
        "(calibrated read, compute and write rates, the forecast) is what the policy consults. The coordinator "
        "posts the task to the chosen worker; the worker reads its inputs from Ceph (RADOS pools, one per "
        "storage node), runs the function and writes each output to the pool the policy chose. Three of the 8 "
        "storage nodes are drawn (0, 1, 7). Dashed brown: a worker reading an input that sits in another "
        "node's pool, which crosses the network. Navy: submit and results "
        "between client and coordinator. Blue worker boxes = workers on the storage nodes (combined).")
    slide = blank_slide(presentation, notes)
    cx, cy, cw, ch = CLIENT_BOX
    add_box(slide, "client", cx, cy, cw, ch, CLIENT_FILL, NAVY)
    ox, oy, ow, oh = COORDINATOR_BOX
    add_container(slide, ox, oy, ow, oh)
    add_text(slide, "coordinator", ox + 0.25, oy + 0.15, 3.0, 0.4, NODE_PT, INK, MEDIUM, PP_ALIGN.LEFT)
    inner_x, inner_w = ox + 0.3, ow - 0.6
    policy_y, model_y, inner_h = oy + 0.75, oy + 2.2, 0.85
    add_box(slide, "policy", inner_x, policy_y, inner_w, inner_h, CS_TINT, CS)
    add_box(slide, "cost model", inner_x, model_y, inner_w, inner_h, PANEL, PANEL_LINE)
    add_line(slide, ox + ow / 2, policy_y + inner_h, ox + ow / 2, model_y, CONNECTOR, both=True)
    add_line(slide, cx + cw, cy + 0.35, ox, cy + 0.35, NAVY, arrow=True)
    add_line(slide, ox, cy + ch - 0.35, cx + cw, cy + ch - 0.35, NAVY, arrow=True)
    trunk_x = 7.9
    worker_w, worker_h = 1.75, 0.72
    store_w, store_h = 1.55, 0.86
    worker_xs = []
    for label, node_y in zip(ARCH_NODES, ARCH_NODE_YS):
        add_container(slide, ARCH_NODE_X, node_y, ARCH_NODE_W, ARCH_NODE_H)
        add_text(slide, label, ARCH_NODE_X + 0.2, node_y + 0.08, 2.0, 0.32, INNER_PT, INK, MEDIUM, PP_ALIGN.LEFT)
        worker_x = ARCH_NODE_X + 0.3
        worker_y = node_y + 0.5
        add_box(slide, WORKER_LABEL, worker_x, worker_y, worker_w, worker_h, CS_TINT, CS)
        store_x = ARCH_NODE_X + ARCH_NODE_W - 0.3 - store_w
        store_y = node_y + 0.42
        add_store(slide, "pool", store_x, store_y, store_w, store_h)
        middle = worker_y + worker_h / 2
        add_line(slide, worker_x + worker_w, middle, store_x, middle, CONNECTOR, both=True)
        add_path(slide, [(trunk_x, middle), (worker_x, middle)], CONNECTOR)
        worker_xs.append((worker_x, worker_y, store_x, store_y))
    first_middle = ARCH_NODE_YS[0] + 0.5 + worker_h / 2
    last_middle = ARCH_NODE_YS[-1] + 0.5 + worker_h / 2
    add_line(slide, ox + ow, oy + oh / 2, trunk_x, oy + oh / 2, CONNECTOR)
    add_line(slide, trunk_x, first_middle, trunk_x, last_middle, CONNECTOR)
    add_text(slide, "⋮", ARCH_NODE_X, ARCH_GAP_Y, ARCH_NODE_W, 0.42, NODE_PT, SECONDARY)
    worker_x, worker_y, _, _ = worker_xs[0]
    _, _, store_x, store_y = worker_xs[1]
    turn_y = ARCH_NODE_YS[1] - 0.14
    dependency_x = worker_x + worker_w * 0.7
    add_path(slide, [(store_x + store_w / 2, store_y), (store_x + store_w / 2, turn_y), (dependency_x, turn_y),
                     (dependency_x, worker_y + worker_h)], BASELINE, dash=MSO_LINE_DASH_STYLE.DASH)
    return slide


# Lab diagram -------------------------------------------------------------------------------

NODES = 8
NODE_PITCH = FIGURE_W / NODES
NODE_W = 1.32
STORAGE_ROW_Y, STORAGE_NODE_H = 1.95, 1.75
COMPUTE_ROW_Y, COMPUTE_NODE_H = 5.75, 0.95
PART_INSET = 0.1
WORKER_H = 0.55
STORE_H = 0.85
ROUTER_W, ROUTER_H = 1.8, 0.62
ROUTER_X = FIGURE_X + FIGURE_W / 2 - ROUTER_W / 2
ROUTER_Y = 4.25
LINK_PT = 1.25
CROSSING_PT = 1.5


def node_x(index):
    return FIGURE_X + index * NODE_PITCH + (NODE_PITCH - NODE_W) / 2


def router_port_x(index):
    return ROUTER_X + 0.15 + index * (ROUTER_W - 0.3) / (NODES - 1)


def draw_lab(slide, computational_storage):
    for index in range(NODES):
        centre = node_x(index) + NODE_W / 2
        if computational_storage:
            add_line(slide, centre, STORAGE_ROW_Y + STORAGE_NODE_H, router_port_x(index), ROUTER_Y, AXIS_LINE,
                     LINK_PT)
            add_line(slide, router_port_x(index), ROUTER_Y + ROUTER_H, centre, COMPUTE_ROW_Y, AXIS_LINE, LINK_PT,
                     faded=True)
        else:
            add_line(slide, centre, STORAGE_ROW_Y + STORAGE_NODE_H, router_port_x(index), ROUTER_Y, BASELINE,
                     CROSSING_PT, arrow=True, dash=MSO_LINE_DASH_STYLE.DASH)
            add_line(slide, router_port_x(index), ROUTER_Y + ROUTER_H, centre, COMPUTE_ROW_Y, BASELINE,
                     CROSSING_PT, arrow=True, dash=MSO_LINE_DASH_STYLE.DASH)
    for index in range(NODES):
        x = node_x(index)
        part_x = x + PART_INSET
        part_w = NODE_W - 2 * PART_INSET
        add_container(slide, x, STORAGE_ROW_Y, NODE_W, STORAGE_NODE_H)
        worker_y = STORAGE_ROW_Y + PART_INSET + 0.05
        store_y = STORAGE_ROW_Y + STORAGE_NODE_H - PART_INSET - STORE_H
        if computational_storage:
            add_box(slide, WORKER_LABEL, part_x, worker_y, part_w, WORKER_H, CS_TINT, CS, INNER_PT)
        else:
            add_box(slide, WORKER_LABEL, part_x, worker_y, part_w, WORKER_H, PANEL, PANEL_LINE, INNER_PT, faded=True)
        add_store(slide, STORAGE_LABEL, part_x, store_y, part_w, STORE_H, INNER_PT)
        add_container(slide, x, COMPUTE_ROW_Y, NODE_W, COMPUTE_NODE_H, faded=computational_storage)
        compute_worker_y = COMPUTE_ROW_Y + (COMPUTE_NODE_H - WORKER_H) / 2
        if computational_storage:
            add_box(slide, WORKER_LABEL, part_x, compute_worker_y, part_w, WORKER_H, PANEL, PANEL_LINE, INNER_PT,
                    faded=True)
        else:
            add_box(slide, WORKER_LABEL, part_x, compute_worker_y, part_w, WORKER_H, BASELINE_TINT, BASELINE,
                    INNER_PT)
    add_box(slide, ROUTER_LABEL, ROUTER_X, ROUTER_Y, ROUTER_W, ROUTER_H, PANEL, PANEL_LINE)
    add_text(slide, LINK_LABEL, FIGURE_X, ROUTER_Y, ROUTER_X - FIGURE_X - 0.45, ROUTER_H, LABEL_PT, SECONDARY,
             align=PP_ALIGN.RIGHT)


LAB_NOTES = (
    "Figure only; no data plotted. Lab: cephdag8 on aakashpc, one Ceph cluster (Ceph 20.2.4): "
    "8 storage nodes (top row), each with an OSD ('storage', the cylinder) and a worker, and 8 compute-only "
    "nodes (bottom row), all 16 containers linked through one router, every link shaped to 400 Mbit/s (tbf; "
    "about 378 Mbit/s goodput, 47 MB/s). Source: demo-data/SETUP.md and DEMO-DATA.md ('Lab'). The storage "
    "row is drawn identically in both states. "
)


def lab_combined(presentation):
    notes = LAB_NOTES + (
        "Combined: tasks run on the workers of the storage nodes (blue); the compute-only nodes are idle "
        "(faded). In the slot-matched runs each node runs 2 workers.")
    slide = blank_slide(presentation, notes)
    draw_lab(slide, computational_storage=True)
    return slide


def lab_separate(presentation):
    notes = LAB_NOTES + (
        "Separate: the same Ceph cluster and data, with the workers moved to the compute-only nodes (brown); "
        "the storage-node workers are idle (faded). Every task reads its input over the network and writes its "
        "output back; the dashed brown arrows show the read direction (outputs cross the links the other way). "
        "Separate moves 21.47 GB per 16 GiB run (t1_slots.csv).")
    slide = blank_slide(presentation, notes)
    draw_lab(slide, computational_storage=False)
    return slide


# Chart helpers -----------------------------------------------------------------------------


def add_chart(slide, chart_type, data, x=FIGURE_X, y=FIGURE_Y, w=FIGURE_W, h=FIGURE_H):
    frame = slide.shapes.add_chart(chart_type, Inches(x), Inches(y), Inches(w), Inches(h), data)
    chart = frame.chart
    chart.has_legend = False
    chart.has_title = False
    chart.font.name = SANS
    chart.font.size = Pt(TICK_PT)
    chart.font.color.rgb = rgb(INK)
    for parent in (chart._chartSpace, chart._chartSpace.chart.plotArea):
        no_fill(parent)
    return chart


def no_fill(parent):
    sp_pr = parent.find(qn("c:spPr"))
    if sp_pr is None:
        sp_pr = etree.SubElement(parent, qn("c:spPr"))
        if parent.tag == qn("c:chartSpace"):
            parent.remove(sp_pr)
            parent.insert(list(parent).index(parent.find(qn("c:chart"))) + 1, sp_pr)
    for child in list(sp_pr):
        sp_pr.remove(child)
    etree.SubElement(sp_pr, qn("a:noFill"))
    line = etree.SubElement(sp_pr, qn("a:ln"))
    etree.SubElement(line, qn("a:noFill"))


def span_gaps(chart):
    blanks = chart._chartSpace.chart.find(qn("c:dispBlanksAs"))
    if blanks is None:
        blanks = etree.SubElement(chart._chartSpace.chart, qn("c:dispBlanksAs"))
    blanks.set("val", "span")


def remove_auto_title(chart):
    auto_title_deleted = chart._chartSpace.chart.find(qn("c:autoTitleDeleted"))
    if auto_title_deleted is not None:
        auto_title_deleted.set("val", "1")


def style_axis(axis, title, gridlines=False):
    axis.has_title = title is not None
    if title is not None:
        axis.axis_title.text_frame.text = title
        set_font(axis.axis_title.text_frame.paragraphs[0].runs[0].font, AXIS_TITLE_PT, SECONDARY)
    set_font(axis.tick_labels.font, TICK_PT, INK)
    axis.format.line.color.rgb = rgb(AXIS_LINE)
    axis.format.line.width = Pt(1.0)
    axis.has_major_gridlines = gridlines
    axis.has_minor_gridlines = False
    if gridlines:
        axis.major_gridlines.format.line.color.rgb = rgb(GRIDLINE)
        axis.major_gridlines.format.line.width = Pt(1.0)


def set_plot_layout(chart, x, y, w, h):
    """Fix the plot area (inside the axes) as fractions of the chart frame, so overlays line up."""
    plot_area = chart._chartSpace.chart.plotArea
    layout = plot_area.find(qn("c:layout"))
    if layout is None:
        layout = etree.Element(qn("c:layout"))
        plot_area.insert(0, layout)
    for child in list(layout):
        layout.remove(child)
    manual = etree.SubElement(layout, qn("c:manualLayout"))
    for tag, value in (("layoutTarget", "inner"), ("xMode", "edge"), ("yMode", "edge"),
                       ("x", x), ("y", y), ("w", w), ("h", h)):
        element = etree.SubElement(manual, qn("c:" + tag))
        element.set("val", str(value))


class PlotBox:
    """Slide coordinates of a chart's plot area, from the frame and the manual layout fractions."""

    def __init__(self, frame_x, frame_y, frame_w, frame_h, layout, y_min, y_max):
        self.x = frame_x + layout[0] * frame_w
        self.y = frame_y + layout[1] * frame_h
        self.w = layout[2] * frame_w
        self.h = layout[3] * frame_h
        self.y_min = y_min
        self.y_max = y_max

    def slide_y(self, value):
        return self.y + self.h * (1 - (value - self.y_min) / (self.y_max - self.y_min))

    def category_x(self, index, count):
        return self.x + (index + 0.5) * self.w / count


def style_line_series(series, colour, width_pt=3.0, marker=True, dash=None):
    series.format.line.color.rgb = rgb(colour)
    series.format.line.width = Pt(width_pt)
    if dash is not None:
        series.format.line.dash_style = dash
    series.smooth = False
    if marker:
        series.marker.style = XL_MARKER_STYLE.CIRCLE
        series.marker.size = 10
        series.marker.format.fill.solid()
        series.marker.format.fill.fore_color.rgb = rgb(colour)
        series.marker.format.line.color.rgb = rgb(colour)
    else:
        series.marker.style = XL_MARKER_STYLE.NONE


def label_points(series, position, number_format, colour, size=LABEL_PT):
    labels = DataLabels(series._element.get_or_add_dLbls())
    labels.show_value = True
    labels.number_format = number_format
    labels.number_format_is_linked = False
    labels.position = position
    set_font(labels.font, size, colour, MEDIUM)


def custom_point_label(point, text, position, colour, size=LABEL_PT, name=MEDIUM):
    label = point.data_label
    label.has_text_frame = True
    label.text_frame.text = text
    for paragraph in label.text_frame.paragraphs:
        for run in paragraph.runs:
            set_font(run.font, size, colour, name)
    label.position = position


def colour_bar(point, colour):
    point.format.fill.solid()
    point.format.fill.fore_color.rgb = rgb(colour)
    point.format.line.fill.background()


def reference_line(slide, box, value, label):
    y = box.slide_y(value)
    add_line(slide, box.x, y, box.x + box.w, y, REFERENCE, 1.5, dash=MSO_LINE_DASH_STYLE.DASH)
    add_text(slide, label, box.x + box.w + 0.1, y - 0.18, 1.6, 0.36, LABEL_PT, REFERENCE, align=PP_ALIGN.LEFT)


# Speedup against arithmetic intensity --------------------------------------------------------

SPEEDUP_FORMAT = '0.00"×"'
SPEEDUP_LAYOUT = (0.08, 0.04, 0.86, 0.76)
SPEEDUP_Y_MAX = 6
STEEP_SLOPE = 0.3
POINT_LABEL_OFFSET = (0.14, 0.06)
POINT_LABEL_H = 0.36


def powers_of_four(largest):
    values = [1]
    while values[-1] < largest:
        values.append(values[-1] * 4)
    return values


def speedup_text(value):
    """The same text as SPEEDUP_FORMAT, for numbers drawn outside a chart's own labels."""
    return f"{value:.2f}×"


def speedup_chart(slide, categories, series_values):
    """Line chart over evenly spaced categories; for powers of 4 that is a log-4 x axis, read the same everywhere."""
    data = CategoryChartData()
    data.categories = [f"{category:,}" for category in categories]
    for name, values in series_values.items():
        data.add_series(name, values)
    data.add_series(REFERENCE_LABEL, [1 for _ in categories])
    chart = add_chart(slide, XL_CHART_TYPE.LINE_MARKERS, data)
    remove_auto_title(chart)
    span_gaps(chart)
    set_plot_layout(chart, *SPEEDUP_LAYOUT)
    y_axis = chart.value_axis
    style_axis(y_axis, SPEEDUP_TITLE, gridlines=True)
    y_axis.minimum_scale = 0
    y_axis.maximum_scale = SPEEDUP_Y_MAX
    y_axis.major_unit = 1
    y_axis.tick_labels.number_format = '0"×"'
    y_axis.tick_labels.number_format_is_linked = False
    reference = chart.plots[0].series[-1]
    style_line_series(reference, REFERENCE, width_pt=1.5, marker=False, dash=MSO_LINE_DASH_STYLE.DASH)
    return chart


def speedup_box():
    return PlotBox(FIGURE_X, FIGURE_Y, FIGURE_W, FIGURE_H, SPEEDUP_LAYOUT, 0, SPEEDUP_Y_MAX)


def hide_point_label(series, index):
    label = series._element.get_or_add_dLbls().get_or_add_dLbl_for_point(index)
    for child in list(label)[1:]:
        label.remove(child)
    etree.SubElement(label, qn("c:delete")).set("val", "1")


def label_beside_point(slide, box, index, count, value, colour):
    x = box.category_x(index, count) + POINT_LABEL_OFFSET[0]
    y = box.slide_y(value) - POINT_LABEL_OFFSET[1] - POINT_LABEL_H
    add_text(slide, speedup_text(value), x, y, 1.2, POINT_LABEL_H, LABEL_PT, colour, MEDIUM, PP_ALIGN.LEFT)


def falls_steeply(box, values, index):
    """Whether the line arriving at a point falls steeply enough to cross a label centred above the point."""
    earlier = [i for i in range(index) if values[i] is not None]
    if values[index] is None or not earlier:
        return False
    run = (index - earlier[-1]) * box.w / len(values)
    fall = box.slide_y(values[index]) - box.slide_y(values[earlier[-1]])
    return fall / run > STEEP_SLOPE


def label_speedups(slide, line, values, colour):
    """Each value above its point, or above and to the right of it where the arriving line would touch the label."""
    label_points(line, XL_LABEL_POSITION.ABOVE, SPEEDUP_FORMAT, colour)
    box = speedup_box()
    for index, value in enumerate(values):
        if falls_steeply(box, values, index):
            hide_point_label(line, index)
            label_beside_point(slide, box, index, len(values), value, colour)


def speedup_vs_intensity(presentation):
    rows = read_csv("t1_slots.csv")
    measured = {int(row["iterations"]): float(row["cs2 over disagg2 median"]) for row in rows}
    categories = powers_of_four(max(measured))
    notes = (
        "Source: demo3/demo-data/t1_slots.csv, column 'cs2 over disagg2 median' (slot-matched: 16 "
        "combined workers (cs2) against 16 separate workers (disagg2), 16 GiB, 256 tasks; best cs2 policy "
        "paired within repeat, median over repeats). Plotted (iterations: speedup): "
        + "; ".join(f"{row['iterations']}: {row['cs2 over disagg2 median']}x (repeats {row['cs2 over disagg2 per repeat']})"
                    for row in rows)
        + ". x axis: powers of 4 evenly spaced, i.e. a log-4 scale; 4 and 16 iterations were not run and the "
        "line spans them. Dashed line: 1x (the y tick labels it). Separate moves 21.47 GB per run; "
        "combined moves 0-0.13 GB.")
    slide = blank_slide(presentation, notes)
    values = [measured.get(category) for category in categories]
    chart = speedup_chart(slide, categories, {CS_LABEL: values})
    style_axis(chart.category_axis, ITERATIONS_TITLE)
    line = chart.plots[0].series[0]
    style_line_series(line, CS)
    label_speedups(slide, line, values, CS)
    return slide


# Speedup against network speed ---------------------------------------------------------------

NETWORK_SERIES = {"1": ("1 iteration", CS), "256": ("256 iterations", NAVY)}


def speedup_vs_network(presentation):
    rows = read_csv("t6_netsweep.csv")
    speeds = sorted({int(row["link Mbit"]) for row in rows})
    by_intensity = {}
    for row in rows:
        by_intensity.setdefault(row["iterations"], {})[int(row["link Mbit"])] = float(row["speedup median"])
    notes = (
        "Source: demo3/demo-data/t6_netsweep.csv, column 'speedup median' (cs2 heft against disagg2 "
        "fifo-hash, 16 workers each, 8 GiB, 128 tasks, 2 repeats). Plotted (link Mbit/s: speedup): "
        + " | ".join(f"{NETWORK_SERIES[it][0]}: " + ", ".join(f"{s}: {by_intensity[it][s]}x" for s in speeds)
                     for it in NETWORK_SERIES)
        + ". The three speeds are evenly spaced on the x axis; each is 2.5x the previous one, so the "
        "spacing is logarithmic. Blue = 1 iteration, navy = 256 iterations (both are combined over "
        "separate). Dashed line: 1x. Combined (cs2) moves 0 GB, separate (disagg2) 10.74 GB per run.")
    slide = blank_slide(presentation, notes)
    series_values = {name: [by_intensity[it][speed] for speed in speeds] for it, (name, _) in NETWORK_SERIES.items()}
    chart = speedup_chart(slide, speeds, series_values)
    style_axis(chart.category_axis, LINK_SPEED_TITLE)
    for line, (name, colour) in zip(chart.plots[0].series, NETWORK_SERIES.values()):
        style_line_series(line, colour)
        label_speedups(slide, line, series_values[name], colour)
    lower_name, lower_colour = NETWORK_SERIES["256"]
    last_value = series_values[lower_name][-1]
    custom_point_label(chart.plots[0].series[1].points[len(speeds) - 1], speedup_text(last_value),
                       XL_LABEL_POSITION.RIGHT, lower_colour)
    box = speedup_box()
    first_x = box.category_x(0, len(speeds))
    top_name, bottom_name = (NETWORK_SERIES[it] for it in NETWORK_SERIES)
    top_y = box.slide_y(series_values[top_name[0]][0])
    add_text(slide, top_name[0], first_x + 0.7, top_y - 0.42, 2.6, 0.36, LABEL_PT, top_name[1], MEDIUM,
             align=PP_ALIGN.LEFT)
    bottom_y = box.slide_y(series_values[bottom_name[0]][0])
    add_text(slide, bottom_name[0], first_x - 1.3, bottom_y + 0.22, 2.6, 0.36, LABEL_PT, bottom_name[1], MEDIUM)
    return slide


# DROID at 496 GB -----------------------------------------------------------------------------

DROID_CATEGORIES = (CS_LABEL, BASELINE_LABEL)
DROID_LAYOUT = (0.2, 0.08, 0.76, 0.66)
BIG_COLUMNS = (FIGURE_X, 7.0)
BIG_COLUMN_W = 5.6
DIVIDER_X = 6.6
CAPS_Y = 2.38
DROID_CAPS_LABELS = ("WALL TIME", "NETWORK BYTES")


def big_number(slide, x, caps, number, colour, beneath):
    """A caps label, a big number, and beneath it one 20 pt line given as (text, colour) runs."""
    add_text(slide, caps, x, CAPS_Y, BIG_COLUMN_W, 0.28, CAPS_PT, SECONDARY, MONO, PP_ALIGN.LEFT, MSO_ANCHOR.TOP)
    add_text(slide, number, x, 3.0, BIG_COLUMN_W, 1.0, BIG_PT, colour, MEDIUM, PP_ALIGN.LEFT)
    first_text, first_colour = beneath[0]
    box = add_text(slide, first_text, x, 4.05, BIG_COLUMN_W, 0.45, BIG_LABEL_PT, first_colour, SANS, PP_ALIGN.LEFT)
    paragraph = box.text_frame.paragraphs[0]
    for run_text, run_colour in beneath[1:]:
        run = paragraph.add_run()
        run.text = run_text
        set_font(run.font, BIG_LABEL_PT, run_colour, SANS)


def big_number_divider(slide):
    add_line(slide, DIVIDER_X, CAPS_Y, DIVIDER_X, 6.17, AXIS_LINE, 1.0)


def droid_numbers(presentation):
    row = read_csv("t2_droid_totals.csv")[0]
    hours = (float(row["cs data-local h"]), float(row["disagg heft h"]))
    moved = (float(row["data-local GB moved"]), float(row["heft GB moved"]))
    reduction = 1 - moved[0] / moved[1]
    notes = (
        "Source: demo3/demo-data/t2_droid_totals.csv (DROID, "
        f"{row['source GB']} GB source, {int(row['tasks']):,} tasks, {row['batches']} batches, one run per "
        "configuration per batch). Left, wall time: column 'speedup' = "
        f"{row['speedup']}x; beneath it, separate (disagg heft) {hours[1]} h ({row['disagg heft s']} s) -> "
        f"combined (cs data-local) {hours[0]} h ({row['cs data-local s']} s). Right, data moved "
        f"over the network: 1 - {moved[0]} / {moved[1]:,} = {reduction:.2%} less, shown as -{reduction:.1%}; "
        f"beneath it, {moved[1]:,} GB -> {moved[0]} GB. Brown = separate, blue = combined. "
        "One worker per node on both sides, 8 against 8 "
        "(campaign demo3-droid, cs and disagg modes; not the two-per-node cs2/disagg2 setup).")
    slide = blank_slide(presentation, notes)
    wall_time, network_bytes = DROID_CAPS_LABELS
    big_number(slide, BIG_COLUMNS[0], wall_time, speedup_text(float(row["speedup"])), CS,
               ((f"{hours[1]:g} h", BASELINE), (" → ", SECONDARY), (f"{hours[0]:g} h", CS)))
    big_number(slide, BIG_COLUMNS[1], network_bytes, f"−{reduction:.1%}", CS,
               ((f"{moved[1]:,.0f} GB", BASELINE), (" → ", SECONDARY), (f"{moved[0]:g} GB", CS)))
    big_number_divider(slide)
    return slide


# A plan that ignores the shared link ---------------------------------------------------------

BLIND_POLICY = "heft"
BLIND_CAPS_LABELS = ("PLANNED", "MEASURED")


def blind_plan(presentation):
    row = next(row for row in read_csv("t11_heft_plan.csv") if row["policy"] == BLIND_POLICY)
    moved = row["tasks off node 0"]
    notes = (
        "Source: demo3/demo-data/t11_heft_plan.csv, row policy = heft, written by demo3/slides/heft_plan.py: "
        "the sched-skew graph at 1 iteration (the uneven data of t9_skew_shards.csv) and HEFT's static plan, "
        "built with the lab's calibrated models. Left, column 'planned s': the finish time the plan expects, "
        f"{row['planned s']} s, with {moved} of {row['tasks']} tasks moved off node 0 ('tasks off node 0'). "
        f"Right, column 'measured s': the median measured wall time, {row['measured s']} s "
        f"(t4_sched_skew_cells.csv). Beneath it: {moved} moved shards x {row['shard s on the link']} s each "
        f"on node 0's one link ('shard s on the link') = {row['link s for the moves']} s ('link s for the "
        "moves').")
    slide = blank_slide(presentation, notes)
    planned, measured = BLIND_CAPS_LABELS
    big_number(slide, BIG_COLUMNS[0], planned, f"{row['planned s']} s", SECONDARY,
               ((f"{moved} tasks moved off the busy node", INK),))
    big_number(slide, BIG_COLUMNS[1], measured, f"{row['measured s']} s", INK,
               ((f"{moved} × {row['shard s on the link']} s on one link = {row['link s for the moves']} s", INK),))
    big_number_divider(slide)
    return slide


def bar_pair(slide, values, x, w, axis_title, y_max, major_unit):
    data = CategoryChartData()
    data.categories = DROID_CATEGORIES
    data.add_series(axis_title, values)
    chart = add_chart(slide, XL_CHART_TYPE.COLUMN_CLUSTERED, data, x=x, w=w)
    remove_auto_title(chart)
    set_plot_layout(chart, *DROID_LAYOUT)
    plot = chart.plots[0]
    plot.gap_width = 45
    plot.vary_by_categories = False
    style_axis(chart.category_axis, None)
    y_axis = chart.value_axis
    style_axis(y_axis, axis_title, gridlines=True)
    y_axis.minimum_scale = 0
    y_axis.maximum_scale = y_max
    y_axis.major_unit = major_unit
    y_axis.tick_labels.number_format = "#,##0"
    y_axis.tick_labels.number_format_is_linked = False
    series = plot.series[0]
    colour_bar(series.points[0], CS)
    colour_bar(series.points[1], BASELINE)
    series.points[0].format.line.color.rgb = rgb(CS)
    series.points[0].format.line.width = Pt(1.5)
    for index, colour in enumerate((CS, BASELINE)):
        custom_point_label(series.points[index], short(values[index]), XL_LABEL_POSITION.OUTSIDE_END, colour)
    return chart


def droid_totals(presentation):
    row = read_csv("t2_droid_totals.csv")[0]
    hours = (float(row["cs data-local h"]), float(row["disagg heft h"]))
    moved = (float(row["data-local GB moved"]), float(row["heft GB moved"]))
    notes = (
        "Source: demo3/demo-data/t2_droid_totals.csv (DROID, "
        f"{row['source GB']} GB source, {int(row['tasks']):,} tasks, {row['batches']} batches, one run per "
        "configuration per batch). Left, wall time in hours: combined (cs data-local, blue) "
        f"{hours[0]} h ({row['cs data-local s']} s), separate (disagg heft, brown) {hours[1]} h "
        f"({row['disagg heft s']} s), speedup {row['speedup']}x. Right, GB moved over the network: "
        f"{moved[0]} GB against {moved[1]:,} GB. Both axes linear from 0; the combined bar on "
        f"the right is {moved[0] / moved[1]:.2%} of the separate one, so what you see is its 1.5 pt outline. "
        "One worker per node on both sides, 8 against 8 (campaign demo3-droid, cs and disagg modes; "
        "not the two-per-node cs2/disagg2 setup).")
    slide = blank_slide(presentation, notes)
    half = FIGURE_W / 2
    bar_pair(slide, hours, FIGURE_X, half - 0.15, "wall time (h)", 7, 1)
    bar_pair(slide, moved, FIGURE_X + half + 0.15, half - 0.15, "data moved (GB)", 5000, 1000)
    return slide


# The hot node ------------------------------------------------------------------------------

SHARD_SIZE, SHARD_GAP = 0.17, 0.05
SHARDS_PER_ROW = 5
HOT_NODE_Y, HOT_NODE_H = 1.9, 3.75
HOT_ROUTER_Y = 6.3
HOT_LINK_PT = 2.5


def hot_node(presentation):
    rows = read_csv("t9_skew_shards.csv")
    shards = {int(row["node"]): int(row["shards"]) for row in rows}
    even_share = min(shards.values())
    notes = (
        "Source: demo3/demo-data/t9_skew_shards.csv, written by demo3/slides/skew_shards.py from the "
        "sched-skew campaign spec (cephdag sched-gaps:experiments/campaigns/sched-skew.json, placement "
        "[0,0,0,0,0,0,0,1,2,3,4,5,6,7], shard i on node placement[i mod 14], experiments/run.py "
        f"synthetic_dataset), dataset {rows[0]['dataset']}: {sum(shards.values())} shards of "
        f"{rows[0]['shard MiB']} MiB. Plotted, one square per shard (node: shards): "
        + ", ".join(f"{node}: {count}" for node, count in sorted(shards.items()))
        + f". Grey squares: the {even_share} shards every node holds; blue: node 0's other "
        f"{shards[0] - even_share}. Every node has one 400 Mbit/s "
        "link to the router (thin grey); node 0's is drawn heavier: everything that leaves node 0 crosses it.")
    slide = blank_slide(presentation, notes)
    router_x = ROUTER_X
    for node in sorted(shards):
        x = node_x(node)
        centre = x + NODE_W / 2
        heavy = shards[node] > even_share
        add_line(slide, centre, HOT_NODE_Y + HOT_NODE_H, router_port_x(node), HOT_ROUTER_Y,
                 INK if heavy else AXIS_LINE, HOT_LINK_PT if heavy else LINK_PT)
    for node in sorted(shards):
        x = node_x(node)
        add_container(slide, x, HOT_NODE_Y, NODE_W, HOT_NODE_H)
        add_text(slide, str(node), x + 0.12, HOT_NODE_Y + 0.1, 0.4, 0.34, INNER_PT, INK, MONO, PP_ALIGN.LEFT)
        stack_w = SHARDS_PER_ROW * SHARD_SIZE + (SHARDS_PER_ROW - 1) * SHARD_GAP
        left = x + (NODE_W - stack_w) / 2
        bottom = HOT_NODE_Y + HOT_NODE_H - 0.15
        for index in range(shards[node]):
            row, column = divmod(index, SHARDS_PER_ROW)
            square_x = left + column * (SHARD_SIZE + SHARD_GAP)
            square_y = bottom - (row + 1) * SHARD_SIZE - row * SHARD_GAP
            colour = OTHER_BAR if index < even_share else CS
            add_shape(slide, MSO_SHAPE.RECTANGLE, square_x, square_y, SHARD_SIZE, SHARD_SIZE, colour, None, 0)
        rows_used = -(-shards[node] // SHARDS_PER_ROW)
        stack_top = bottom - rows_used * SHARD_SIZE - (rows_used - 1) * SHARD_GAP
        add_text(slide, str(shards[node]), x, stack_top - 0.45, NODE_W, 0.38, LABEL_PT,
                 CS if shards[node] > even_share else SECONDARY, MEDIUM)
    add_box(slide, ROUTER_LABEL, router_x, HOT_ROUTER_Y, ROUTER_W, ROUTER_H, PANEL, PANEL_LINE)
    return slide


# Scheduling with uneven data -----------------------------------------------------------------

SKEW_POLICIES = {"heft": OTHER_BAR, "heft-cc": FOCUS}
SKEW_REFERENCE_POLICY = "data-local"
BARS_LAYOUT = (0.09, 0.08, 0.78, 0.72)


def relative_bars(slide, categories, series_values, y_max, major_unit, axis_title):
    data = CategoryChartData()
    data.categories = categories
    for name, values in series_values.items():
        data.add_series(name, values)
    chart = add_chart(slide, XL_CHART_TYPE.COLUMN_CLUSTERED, data)
    remove_auto_title(chart)
    set_plot_layout(chart, *BARS_LAYOUT)
    plot = chart.plots[0]
    plot.gap_width = 50
    plot.overlap = 0
    plot.vary_by_categories = False
    style_axis(chart.category_axis, ITERATIONS_CATEGORY_TITLE)
    y_axis = chart.value_axis
    style_axis(y_axis, axis_title, gridlines=True)
    y_axis.minimum_scale = 0
    y_axis.maximum_scale = y_max
    y_axis.major_unit = major_unit
    y_axis.tick_labels.number_format = "0.0" if major_unit < 1 else "0"
    y_axis.tick_labels.number_format_is_linked = False
    return chart


def label_bar_series(chart, series_values, colours, number_format, name_first_group=True, decimals=2, inside=()):
    """Value labels above each bar, or in white inside the top of the bars of the series named in `inside`
    (bars that end just under a dashed reference line, which would strike through a label above them)."""
    for series, (name, values), colour in zip(chart.plots[0].series, series_values.items(), colours):
        series.format.fill.solid()
        series.format.fill.fore_color.rgb = rgb(colour)
        series.format.line.fill.background()
        text_colour = "FFFFFF" if name in inside else LABEL_COLOURS.get(colour, colour)
        position = XL_LABEL_POSITION.INSIDE_END if name in inside else XL_LABEL_POSITION.OUTSIDE_END
        label_points(series, position, number_format, text_colour)
        if name_first_group:
            custom_point_label(series.points[0], f"{name}\n{values[0]:.{decimals}f}", position, text_colour)


def scheduling_skew(presentation):
    rows = read_csv("t4_sched_skew_cells.csv")
    medians = {(row["policy"], int(row["iterations"])): float(row["median s"]) for row in rows}
    iterations = sorted({int(row["iterations"]) for row in rows})
    relative = {policy: [round(medians[(policy, it)] / medians[(SKEW_REFERENCE_POLICY, it)], 2)
                         for it in iterations] for policy in SKEW_POLICIES}
    notes = (
        "Source: demo3/demo-data/t4_sched_skew_cells.csv, column 'median s' (sched-skew, 8 GiB, 65 of "
        "128 shards on node 0, 3 repeats). Each bar is the policy's median wall time divided by "
        "data-local's median at the same intensity; data-local = 1 is the dashed line (its bars would "
        "all be 1, so they are drawn as the line). Blue = heft-cc, grey = heft. Medians in s (iterations: heft / "
        "data-local / heft-cc): "
        + "; ".join(f"{it}: {medians[('heft', it)]} / {medians[('data-local', it)]} / {medians[('heft-cc', it)]}"
                    for it in iterations)
        + ". Plotted ratios: " + " | ".join(f"{policy}: {relative[policy]}" for policy in SKEW_POLICIES)
        + ". GB moved (heft / data-local / heft-cc): "
        + "; ".join(f"{it}: {rows_value(rows, 'heft', it)} / {rows_value(rows, 'data-local', it)} / "
                    f"{rows_value(rows, 'heft-cc', it)}" for it in iterations) + ".")
    slide = blank_slide(presentation, notes)
    y_max = 2.2
    chart = relative_bars(slide, [f"{it:,}" for it in iterations], relative, y_max, 0.5, "relative to data-local")
    label_bar_series(chart, relative, SKEW_POLICIES.values(), "0.00", inside={"heft-cc"})
    box = PlotBox(FIGURE_X, FIGURE_Y, FIGURE_W, FIGURE_H, BARS_LAYOUT, 0, y_max)
    reference_line(slide, box, 1, SKEW_REFERENCE_POLICY)
    return slide


def rows_value(rows, policy, iterations):
    for row in rows:
        if row["policy"] == policy and int(row["iterations"]) == iterations:
            return row["GB moved"]
    raise KeyError((policy, iterations))


# LP plan against heft-cc ---------------------------------------------------------------------


def paired_ratio_median(numerators, denominators):
    return statistics.median(n / d for n, d in zip(numerators, denominators))


def lp_against_heft_cc(presentation):
    repeats = {}
    for row in read_csv("t4_sched_lp_low.csv") + read_csv("t4_sched_lp.csv"):
        repeats[(row["policy"], int(row["iterations"]))] = numbers(row["repeats s"])
    iterations = sorted({it for policy, it in repeats if policy == "lp-plan2"})
    paired = [round(paired_ratio_median(repeats[("lp-plan2", it)], repeats[("heft-cc", it)]), 2) for it in iterations]
    of_medians = [round(statistics.median(repeats[("lp-plan2", it)]) / statistics.median(repeats[("heft-cc", it)]), 3)
                  for it in iterations]
    notes = (
        "Sources: demo3/demo-data/t4_sched_lp_low.csv (1 and 16 iterations, Nov 15) and t4_sched_lp.csv "
        "(128 and 1024, Nov 14); skewed placement, 3 repeats, lp-plan2 and heft-cc in the same session. "
        "Each bar is lp-plan2's wall time over heft-cc's in the same repeat, median of the 3 paired "
        "ratios (the data pack's convention), from the 'repeats s' columns. Plotted: "
        + ", ".join(f"{it}: {r}" for it, r in zip(iterations, paired))
        + ". Ratio of the medians instead: " + ", ".join(f"{it}: {r}" for it, r in zip(iterations, of_medians))
        + ". Repeats s (lp-plan2 | heft-cc): "
        + "; ".join(f"{it}: {repeats[('lp-plan2', it)]} | {repeats[('heft-cc', it)]}" for it in iterations)
        + ". Dashed line: heft-cc = 1. Blue = lp-plan2.")
    slide = blank_slide(presentation, notes)
    series_values = {"lp-plan2": paired}
    y_max = 1.2
    chart = relative_bars(slide, [f"{it:,}" for it in iterations], series_values, y_max, 0.2, "relative to heft-cc")
    chart.plots[0].gap_width = 110
    label_bar_series(chart, series_values, [FOCUS], "0.00", inside={"lp-plan2"})
    box = PlotBox(FIGURE_X, FIGURE_Y, FIGURE_W, FIGURE_H, BARS_LAYOUT, 0, y_max)
    reference_line(slide, box, 1, "heft-cc")
    return slide


# Real pipeline with a hot node ---------------------------------------------------------------

PIPELINE_COLOURS = {"data-local": OTHER_BAR, "heft-cc": FOCUS, "lp-plan2": FOCUS, "heft": OTHER_BAR,
                    "fifo-hash": BASELINE}
PLANNING_COLOURS = {OTHER_BAR: OTHER_BAR_LIGHT, FOCUS: FOCUS_LIGHT, BASELINE: BASELINE_LIGHT}
PIPELINE_LAYOUT = (0.14, 0.03, 0.76, 0.78)
PIPELINE_X_MAX = 130
TOTAL_LABEL_SPACE = 22
PLANNING_LABEL = "planning"
PLANNING_LABEL_POLICY = "lp-plan2"


def pipeline_planning(presentation):
    rows = read_csv("t7_sched_droid.csv")
    policies = [row["policy"] for row in rows]
    totals = [float(row["median s"]) for row in rows]
    planning = [round(statistics.mean(numbers(row["planning s per wave"])), 2) for row in rows]
    execution = [round(total - plan, 2) for total, plan in zip(totals, planning)]
    data = CategoryChartData()
    data.categories = [label_for(policy) for policy in reversed(policies)]
    data.add_series("execution", list(reversed(execution)))
    data.add_series(PLANNING_LABEL, list(reversed(planning)))
    data.add_series("total", [TOTAL_LABEL_SPACE for _ in policies])
    notes = (
        "Source: demo3/demo-data/t7_sched_droid.csv (fused DROID, shards 500-515 in two waves, node 0 "
        "holding 54-62% of the data, one run per configuration per wave; the policies run on 8 storage-node "
        "workers (combined); separate = disagg fifo-hash on 8 compute-only workers). Bar length = 'median s' (mean of the two "
        "waves); light segment = planning, mean of 'planning s per wave'; dark segment = the rest. "
        "Blue = heft-cc and lp-plan2, grey = data-local and heft, brown = separate. "
        "Plotted (policy: total s = execution + planning; per wave s): "
        + "; ".join(f"{label_for(p)}: {t} = {e} + {pl} ({row['per wave s']})"
                    for p, t, e, pl, row in zip(policies, totals, execution, planning, rows))
        + ". The rows above 'separate' ran on the storage nodes (combined).")
    slide = blank_slide(presentation, notes)
    chart = add_chart(slide, XL_CHART_TYPE.BAR_STACKED, data)
    remove_auto_title(chart)
    set_plot_layout(chart, *PIPELINE_LAYOUT)
    plot = chart.plots[0]
    plot.gap_width = 55
    plot.overlap = 100
    plot.vary_by_categories = False
    style_axis(chart.category_axis, None)
    x_axis = chart.value_axis
    style_axis(x_axis, SECONDS_TITLE, gridlines=True)
    x_axis.minimum_scale = 0
    x_axis.maximum_scale = PIPELINE_X_MAX
    x_axis.major_unit = 20
    x_axis.tick_labels.number_format = "0"
    x_axis.tick_labels.number_format_is_linked = False
    execution_series, planning_series, total_series = plot.series
    ordered = list(reversed(policies))
    ordered_totals = list(reversed(totals))
    for index, policy in enumerate(ordered):
        colour = PIPELINE_COLOURS[policy]
        colour_bar(execution_series.points[index], colour)
        colour_bar(planning_series.points[index], PLANNING_COLOURS[colour])
        total_series.points[index].format.fill.background()
        total_series.points[index].format.line.fill.background()
        custom_point_label(total_series.points[index], f" {ordered_totals[index]:.1f}", XL_LABEL_POSITION.INSIDE_BASE,
                           LABEL_COLOURS.get(colour, colour))
    planning_index = ordered.index(PLANNING_LABEL_POLICY)
    custom_point_label(planning_series.points[planning_index], PLANNING_LABEL, XL_LABEL_POSITION.CENTER, FOCUS,
                       name=SANS)
    return slide


# Search against a heuristic ------------------------------------------------------------------

SEARCH_POLICIES = {"heft-cc": FOCUS, "native-mcts-heuristic-128": OTHER_BAR}


def search_against_heuristic(presentation):
    rows = read_csv("t5_sched_mcts.csv")
    medians = {(row["policy"], int(row["iterations"])): float(row["median s"]) for row in rows}
    repeats = {(row["policy"], int(row["iterations"])): row["repeats s"] for row in rows}
    iterations = sorted({int(row["iterations"]) for row in rows})
    series_values = {label_for(policy): [medians[(policy, it)] for it in iterations] for policy in SEARCH_POLICIES}
    notes = (
        "Source: demo3/demo-data/t5_sched_mcts.csv, column 'median s' (sched-mcts, skewed placement, 2 "
        "repeats; MCTS-128 = native-mcts-heuristic-128). Blue = heft-cc, grey = MCTS-128. Plotted (iterations: "
        "heft-cc / MCTS-128 s, repeats in brackets): "
        + "; ".join(f"{it}: {medians[('heft-cc', it)]} ({repeats[('heft-cc', it)]}) / "
                    f"{medians[('native-mcts-heuristic-128', it)]} ({repeats[('native-mcts-heuristic-128', it)]})"
                    for it in iterations)
        + ". y axis linear from 0.")
    slide = blank_slide(presentation, notes)
    chart = relative_bars(slide, [f"{it:,}" for it in iterations], series_values, 120, 20, SECONDS_TITLE)
    label_bar_series(chart, series_values, SEARCH_POLICIES.values(), "0.0", decimals=1)
    return slide


# Backup: equal worker counts -----------------------------------------------------------------

WORKER_SERIES = (
    ("16 vs 16 workers", "t1_slots.csv", "cs2 over disagg2 median", CS),
    ("8 vs 16 workers", "t1_overlap.csv", "cs over disagg2 median", NAVY),
)


def equal_worker_counts(presentation):
    measured = {}
    for name, filename, column, _ in WORKER_SERIES:
        measured[name] = {int(row["iterations"]): float(row[column]) for row in read_csv(filename)}
    categories = powers_of_four(max(max(points) for points in measured.values()))
    notes = (
        "Sources: two CSVs from the same day, same separate side (disagg2: 16 workers, 2 per compute node). "
        + " | ".join(f"{name}: demo3/demo-data/{filename}, column '{column}': "
                     + ", ".join(f"{x}: {y}x" for x, y in measured[name].items())
                     for name, filename, column, _ in WORKER_SERIES)
        + ". '8 vs 16 workers' (navy) = 8 combined workers (1 per storage node) against 16; "
        "'16 vs 16 workers' (blue) = 2 per storage node. x axis: powers of 4 evenly spaced (log-4 scale); "
        "missing intensities are spanned by the line. Dashed line: 1x.")
    slide = blank_slide(presentation, notes)
    series_values = {name: [measured[name].get(category) for category in categories] for name, *_ in WORKER_SERIES}
    chart = speedup_chart(slide, categories, series_values)
    style_axis(chart.category_axis, ITERATIONS_TITLE)
    for line, (name, _, _, colour) in zip(chart.plots[0].series, WORKER_SERIES):
        style_line_series(line, colour)
    box = speedup_box()
    first_x = box.category_x(0, len(categories))
    upper, lower = WORKER_SERIES
    upper_y = box.slide_y(measured[upper[0]][1])
    add_text(slide, upper[0], first_x + 0.25, upper_y - 0.45, 2.8, 0.36, LABEL_PT, upper[3], MEDIUM,
             align=PP_ALIGN.LEFT)
    lower_y = box.slide_y(measured[lower[0]][1])
    add_text(slide, lower[0], first_x + 0.25, lower_y + 0.14, 2.8, 0.36, LABEL_PT, lower[3], MEDIUM,
             align=PP_ALIGN.LEFT)
    return slide


# The DROID pipeline on one partition ---------------------------------------------------------

PIPELINE_SOURCE = "episodes"
PIPELINE_STEPS = (("encode_video", "video"), ("frame_stats", "stats"), ("state_parquet", "parquet"))
PIPELINE_FIRST_STEP = "decode_resize"
PIPELINE_READS = {"episodes": 1, "frames": 2, "state": 1}
PIPELINE_DRAWN_TO_SCALE = ("episodes", "frames", "video")
PIPELINE_XS = {"source": FIGURE_X, "first": 3.63, "stored": 5.85, "second": 8.81, "final": 10.66}
PIPELINE_TOP = 2.3
PIPELINE_STATE_DROP = 1.1
PIPELINE_BOX_W = 2.0
PIPELINE_INCH_PER_MB = 0.034
PIPELINE_SLIVER_H = 0.08
PIPELINE_SLIVER_FILL = AXIS_LINE
PIPELINE_ARROW_GAP = 0.18
PIPELINE_JOIN_X = 5.03
PIPELINE_JOIN_PT = 1.5
PIPELINE_FLOW_PT = 1.8
FUNCTION_TILE = 0.89
FUNCTION_GLYPH, FUNCTION_GLYPH_PT, FUNCTION_GLYPH_LIFT = "ƒ", 47.36, 0.03
CAPTION_H, CAPTION_GAP = 0.28, 0.06


def megabytes_label(megabytes):
    return f"{megabytes:.0f} MB"


def data_box(slide, name, megabytes, x, centre_y):
    """A stored object as a grey box whose height is its size; its name and size inside."""
    height = megabytes * PIPELINE_INCH_PER_MB
    add_shape(slide, MSO_SHAPE.RECTANGLE, x, centre_y - height / 2, PIPELINE_BOX_W, height, PANEL, PANEL, 1)
    add_text(slide, name, x, centre_y - 0.33, PIPELINE_BOX_W, 0.34, NODE_PT)
    add_text(slide, megabytes_label(megabytes), x, centre_y + 0.03, PIPELINE_BOX_W, CAPTION_H, SMALL_PT, SECONDARY, MONO)


def data_sliver(slide, name, x, centre_y):
    """A stored object too small to draw to scale: a thin bar with its name beneath."""
    top = centre_y - PIPELINE_SLIVER_H / 2
    add_shape(slide, MSO_SHAPE.RECTANGLE, x, top, PIPELINE_BOX_W, PIPELINE_SLIVER_H, PIPELINE_SLIVER_FILL, None, 0)
    add_text(slide, name, x, top + PIPELINE_SLIVER_H + CAPTION_GAP, PIPELINE_BOX_W, CAPTION_H, SMALL_PT, INK, MONO,
             PP_ALIGN.LEFT)


def function_tile(slide, name, x, centre_y):
    top = centre_y - FUNCTION_TILE / 2
    add_shape(slide, MSO_SHAPE.RECTANGLE, x, top, FUNCTION_TILE, FUNCTION_TILE, CS_TINT, CS_TINT, 1)
    add_text(slide, FUNCTION_GLYPH, x, top - FUNCTION_GLYPH_LIFT, FUNCTION_TILE, FUNCTION_TILE, FUNCTION_GLYPH_PT, CS,
             anchor=MSO_ANCHOR.TOP)
    caption_x = x + FUNCTION_TILE / 2 - PIPELINE_BOX_W / 2
    add_text(slide, name, caption_x, top + FUNCTION_TILE + CAPTION_GAP, PIPELINE_BOX_W, CAPTION_H, SMALL_PT, INK, MONO)


def flow(slide, from_x, to_x, y):
    add_line(slide, from_x + PIPELINE_ARROW_GAP, y, to_x - PIPELINE_ARROW_GAP, y, CS, PIPELINE_FLOW_PT, arrow=True)


def pipeline_traffic_gb(rows):
    """GB read and written over the run: every output is written once, every input read by each of its tasks."""
    written = sum(float(row["GB"]) for name, row in rows.items() if name != PIPELINE_SOURCE)
    read = sum(float(rows[name]["GB"]) * readers for name, readers in PIPELINE_READS.items())
    return written + read


def droid_pipeline(presentation):
    rows = {row["object"]: row for row in read_csv("t10_droid_objects.csv")}
    baseline_moved = float(read_csv("t2_droid_totals.csv")[0]["heft GB moved"])
    megabytes = {name: float(row["MB per partition"]) for name, row in rows.items()}
    source = rows[PIPELINE_SOURCE]
    notes = (
        "Source: demo3/demo-data/t10_droid_objects.csv, written by demo3/slides/droid_objects.py from the "
        "per-run records of the 496 GB DROID run (~/datasets/cephdag-demo3/campaigns/demo3-droid/wave*-cs/a1/"
        "wave-*/run-*-data-local.json, the stored size of every task output). The graph is the pipeline as run "
        "(cephdag experiments/droid_workload.py build_graph; functions in cephdag/droid.py): for each partition "
        "of whole episodes, decode_resize writes frames and state; encode_video and frame_stats each read the "
        "frames; state_parquet reads the state. Four tasks per partition: "
        f"{int(source['partitions']):,} partitions, {int(source['tasks']):,} tasks. Box heights are "
        "proportional to mean bytes per partition: "
        + ", ".join(f"{name} {megabytes[name]:.1f} MB" for name in PIPELINE_DRAWN_TO_SCALE)
        + ". The thin bars are not to scale: "
        + ", ".join(f"{name} {megabytes[name] * 1000:.0f} kB" for name in megabytes
                    if name not in PIPELINE_DRAWN_TO_SCALE)
        + ". Whole run: "
        + ", ".join(f"{name} {float(row['GB']):,.1f} GB ({float(row['times source']):.2f}x the source)"
                    for name, row in rows.items())
        + ". Every arrow is a read or a write of the object it touches (frames are read twice); in the "
        f"separate system each one crosses the network. Over the run the arrows add up to "
        f"{pipeline_traffic_gb(rows):,.0f} GB; the separate run's measured network payload was "
        f"{baseline_moved:,.0f} GB (demo3/demo-data/t2_droid_totals.csv).")
    slide = blank_slide(presentation, notes)
    xs = PIPELINE_XS
    frames_h = megabytes["frames"] * PIPELINE_INCH_PER_MB
    middle = PIPELINE_TOP + frames_h / 2
    row_y = {
        "video": PIPELINE_TOP + FUNCTION_TILE / 2,
        "stats": PIPELINE_TOP + frames_h - FUNCTION_TILE / 2,
        "parquet": PIPELINE_TOP + frames_h + PIPELINE_STATE_DROP,
    }
    data_box(slide, PIPELINE_SOURCE, megabytes[PIPELINE_SOURCE], xs["source"], middle)
    flow(slide, xs["source"] + PIPELINE_BOX_W, xs["first"], middle)
    function_tile(slide, PIPELINE_FIRST_STEP, xs["first"], middle)
    add_line(slide, xs["first"] + FUNCTION_TILE + PIPELINE_ARROW_GAP, middle, PIPELINE_JOIN_X, middle, AXIS_LINE,
             PIPELINE_JOIN_PT)
    add_line(slide, PIPELINE_JOIN_X, middle, PIPELINE_JOIN_X, row_y["parquet"], AXIS_LINE, PIPELINE_JOIN_PT)
    for y in (middle, row_y["parquet"]):
        flow(slide, PIPELINE_JOIN_X - PIPELINE_ARROW_GAP, xs["stored"], y)
    data_box(slide, "frames", megabytes["frames"], xs["stored"], middle)
    data_sliver(slide, "state", xs["stored"], row_y["parquet"])
    for name, output in PIPELINE_STEPS:
        y = row_y[output]
        flow(slide, xs["stored"] + PIPELINE_BOX_W, xs["second"], y)
        function_tile(slide, name, xs["second"], y)
        flow(slide, xs["second"] + FUNCTION_TILE, xs["final"], y)
        if output in PIPELINE_DRAWN_TO_SCALE:
            data_box(slide, output, megabytes[output], xs["final"], y)
        else:
            data_sliver(slide, output, xs["final"], y)
    return slide


# What the scheduler sees and decides ---------------------------------------------------------

VIEW_INPUTS = (
    ("task graph", "tasks, sizes, compute"),
    ("cluster state", "where data is, who is busy"),
    ("measured rates", "disk, link, CPU"),
)
VIEW_INPUT_X, VIEW_INPUT_W, VIEW_INPUT_H = 0.667, 3.194, 1.1
VIEW_INPUT_YS = (2.306, 3.839, 5.372)
VIEW_INPUT_NAME_PT, VIEW_INPUT_CAPTION_PT = 18, 13
VIEW_FLOW_Y = 4.389
VIEW_JOIN_XS = (4.181, 4.861)
VIEW_ARROW_IN_END = 5.694
VIEW_POLICY_BOX = (5.931, 3.889, 3.028, 1.0)
VIEW_POLICY, VIEW_POLICY_CAPTION = "scheduling policy", "choose(state)"
VIEW_POLICY_PT, VIEW_CAPTION_PT = 20, 14
VIEW_ARROW_OUT = (9.194, 9.708)
VIEW_DECISION_BOX = (9.944, 2.306, 2.722, 4.444)
VIEW_DECISION_TITLE = "Dispatch"
VIEW_DECISION_PARTS = ("task", "worker", "output nodes")
VIEW_PART_X, VIEW_PART_W, VIEW_PART_H = 10.222, 2.167, 0.667
VIEW_PART_YS = (3.194, 4.181, 5.167)
VIEW_PART_PT = 17
VIEW_DECISION_CAPTION = "or wait"


def scheduler_view(presentation):
    notes = (
        "Figure only; no numbers plotted. Source: the cephdag README, section 'What the coordinator decides', "
        "and cephdag/policies/__init__.py. At every event the coordinator rebuilds its picture of the live "
        "cluster, a model.Simulation (the task graph, where each output is stored, which workers and links are "
        "busy, the calibrated disk, link and CPU rates), and calls policy.choose(state). The policy returns "
        "Dispatch(task, worker, targets): run this task on that worker and store output i on node targets[i]. "
        "Or it returns None, which means wait for the next event.")
    slide = blank_slide(presentation, notes)
    trunk_x = VIEW_JOIN_XS[1]
    centres = []
    for (name, caption), y in zip(VIEW_INPUTS, VIEW_INPUT_YS):
        add_shape(slide, MSO_SHAPE.RECTANGLE, VIEW_INPUT_X, y, VIEW_INPUT_W, VIEW_INPUT_H, PANEL, PANEL, 1)
        add_text(slide, name, VIEW_INPUT_X, y + 0.24, VIEW_INPUT_W, 0.35, VIEW_INPUT_NAME_PT)
        add_text(slide, caption, VIEW_INPUT_X, y + 0.62, VIEW_INPUT_W, 0.26, VIEW_INPUT_CAPTION_PT, SECONDARY, MONO)
        centre = y + VIEW_INPUT_H / 2
        add_line(slide, VIEW_JOIN_XS[0], centre, trunk_x, centre, AXIS_LINE, PIPELINE_JOIN_PT)
        centres.append(centre)
    add_line(slide, trunk_x, centres[0], trunk_x, centres[-1], AXIS_LINE, PIPELINE_JOIN_PT)
    add_line(slide, trunk_x, VIEW_FLOW_Y, VIEW_ARROW_IN_END, VIEW_FLOW_Y, CS, PIPELINE_FLOW_PT, arrow=True)
    x, y, w, h = VIEW_POLICY_BOX
    add_shape(slide, MSO_SHAPE.RECTANGLE, x, y, w, h, CS_TINT, CS_TINT, 1)
    add_text(slide, VIEW_POLICY, x, y + 0.17, w, 0.39, VIEW_POLICY_PT, CS)
    add_text(slide, VIEW_POLICY_CAPTION, x, y + 0.58, w, 0.28, VIEW_CAPTION_PT, CS, MONO)
    add_line(slide, VIEW_ARROW_OUT[0], VIEW_FLOW_Y, VIEW_ARROW_OUT[1], VIEW_FLOW_Y, CS, PIPELINE_FLOW_PT, arrow=True)
    x, y, w, h = VIEW_DECISION_BOX
    add_shape(slide, MSO_SHAPE.RECTANGLE, x, y, w, h, "FFFFFF", AXIS_LINE, 1)
    add_text(slide, VIEW_DECISION_TITLE, x + 0.223, y + 0.194, w - 0.444, 0.36, NODE_PT, INK, MEDIUM, PP_ALIGN.LEFT,
             MSO_ANCHOR.TOP)
    for part, part_y in zip(VIEW_DECISION_PARTS, VIEW_PART_YS):
        add_shape(slide, MSO_SHAPE.RECTANGLE, VIEW_PART_X, part_y, VIEW_PART_W, VIEW_PART_H, PANEL, PANEL, 1)
        add_text(slide, part, VIEW_PART_X, part_y, VIEW_PART_W, VIEW_PART_H, VIEW_PART_PT)
    add_text(slide, VIEW_DECISION_CAPTION, x + 0.362, y + 3.958, w - 0.43, 0.28, VIEW_CAPTION_PT, INK, MONO,
             PP_ALIGN.LEFT, MSO_ANCHOR.TOP)
    return slide


# What we found -------------------------------------------------------------------------------

FINDINGS_COLUMNS = (0.67, 4.75, 8.83)
FINDINGS_COLUMN_W = 3.84
FINDINGS_DIVIDER_GAP = 0.24
FINDINGS_TOP, FINDINGS_BOTTOM = 2.417, 6.57
FINDINGS_NUMBER_Y, FINDINGS_STATEMENT_Y = 3.9, 5.806
FINDINGS_LABEL_PT, FINDINGS_NUMBER_PT, FINDINGS_STATEMENT_PT = 14, 30, 23
FINDINGS_LABELS = ("PHYSICAL LOCALITY", "ARITHMETIC INTENSITY", "STRAGGLER NODES")
FINDINGS_STATEMENTS = (
    ("faster on", "{source_gb:.0f} GB of DROID"),
    ("light tasks", "gain the most"),
    ("book the link", "before moving work"),
)
FINDINGS_STRAGGLER_POLICIES = ("heft", "heft-cc")


def add_lines(slide, lines, x, y, w, h, size, colour, name):
    box = add_text(slide, lines[0], x, y, w, h, size, colour, name, PP_ALIGN.LEFT, MSO_ANCHOR.TOP)
    for line in lines[1:]:
        paragraph = box.text_frame.add_paragraph()
        paragraph.alignment = PP_ALIGN.LEFT
        run = paragraph.add_run()
        run.text = line
        set_font(run.font, size, colour, name)
    return box


def findings(presentation):
    droid = read_csv("t2_droid_totals.csv")[0]
    slots = {int(row["iterations"]): float(row["cs2 over disagg2 median"]) for row in read_csv("t1_slots.csv")}
    measured = {row["policy"]: row["measured s"] for row in read_csv("t11_heft_plan.csv")}
    lightest, heaviest = min(slots), max(slots)
    blind, link_aware = FINDINGS_STRAGGLER_POLICIES
    shown = (
        speedup_text(float(droid["speedup"])),
        f"{speedup_text(slots[lightest])} → {speedup_text(slots[heaviest])}",
        f"{measured[blind]} s → {measured[link_aware]} s",
    )
    notes = (
        "Sources, left to right. Physical locality: demo3/demo-data/t2_droid_totals.csv, column 'speedup' = "
        f"{droid['speedup']}x on {droid['source GB']} GB of DROID (separate {droid['disagg heft h']} h, combined "
        f"{droid['cs data-local h']} h). Arithmetic intensity: demo3/demo-data/t1_slots.csv, column 'cs2 over "
        f"disagg2 median' at {lightest} iteration ({slots[lightest]}x) and at {heaviest:,} iterations "
        f"({slots[heaviest]}x). Straggler nodes: demo3/demo-data/t11_heft_plan.csv, column 'measured s' on the "
        f"uneven data at 1 iteration: {blind} {measured[blind]} s, {link_aware} {measured[link_aware]} s.")
    slide = blank_slide(presentation, notes)
    source_gb = float(droid["source GB"])
    for x, label, number, statement in zip(FINDINGS_COLUMNS, FINDINGS_LABELS, shown, FINDINGS_STATEMENTS):
        add_text(slide, label, x, FINDINGS_TOP, FINDINGS_COLUMN_W, 0.28, FINDINGS_LABEL_PT, INK, MONO, PP_ALIGN.LEFT,
                 MSO_ANCHOR.TOP)
        add_text(slide, number, x, FINDINGS_NUMBER_Y, FINDINGS_COLUMN_W, 0.56, FINDINGS_NUMBER_PT, CS, MEDIUM,
                 PP_ALIGN.LEFT, MSO_ANCHOR.TOP)
        lines = [line.format(source_gb=source_gb) for line in statement]
        add_lines(slide, lines, x, FINDINGS_STATEMENT_Y, FINDINGS_COLUMN_W, 0.875, FINDINGS_STATEMENT_PT, INK, MEDIUM)
    for x in FINDINGS_COLUMNS[1:]:
        divider_x = x - FINDINGS_DIVIDER_GAP
        add_line(slide, divider_x, FINDINGS_TOP, divider_x, FINDINGS_BOTTOM, AXIS_LINE, 1.0)
    return slide


FIGURES = (
    task_stages,
    client_code,
    software_architecture,
    lab_combined,
    lab_separate,
    speedup_vs_intensity,
    speedup_vs_network,
    droid_numbers,
    droid_totals,
    hot_node,
    blind_plan,
    scheduling_skew,
    lp_against_heft_cc,
    pipeline_planning,
    search_against_heuristic,
    equal_worker_counts,
    droid_pipeline,
    scheduler_view,
    findings,
)


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "figures.pptx"
    presentation = Presentation()
    presentation.slide_width = Inches(SLIDE_W)
    presentation.slide_height = Inches(SLIDE_H)
    for figure in FIGURES:
        figure(presentation)
    presentation.save(output)
    print(output, len(presentation.slides), "slides")


if __name__ == "__main__":
    main()
