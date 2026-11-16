"""Tests for the deck tools: run with `$PY -m unittest test_slides` from this directory."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

import add_slide
import build_deck
import droid_objects
import make_slide_figures as figures

HEADING_BOTTOM = Inches(1.8)


def output(dtype, size):
    return {"dtype": dtype, "size": size}


def task(*outputs):
    return {"result": {"outputs": list(outputs)}}


def blank_deck():
    presentation = Presentation()
    presentation.slide_width = Inches(figures.SLIDE_W)
    presentation.slide_height = Inches(figures.SLIDE_H)
    return presentation


def deck_without_notes_placeholders():
    """A deck like his Demo 1 file: its notes master offers a new notes slide nothing to clone."""
    presentation = Presentation()
    tree = presentation.notes_master.shapes._spTree
    for placeholder in list(presentation.notes_master.placeholders):
        tree.remove(placeholder._element)
    return presentation


def texts(slide):
    return [shape.text_frame.text for shape in slide.shapes if shape.has_text_frame]


def notes(slide):
    return slide.notes_slide.notes_text_frame.text


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DroidObjects(unittest.TestCase):
    def test_sizes_are_summed_per_object_over_every_run(self):
        first = {
            "dataset_bytes": 100,
            "tasks": {
                "t0": task(output("droid-frames", 210), output("droid-state", 2)),
                "t1": task(output("droid-video", 70)),
                "t2": task(output("droid-frames", 230), output("droid-state", 4)),
            },
        }
        second = {"dataset_bytes": 50, "tasks": {"t0": task(output("droid-frames", 110), output("droid-state", 1))}}
        size, partitions, tasks = droid_objects.totals([first, second])
        self.assertEqual(dict(size), {"episodes": 150, "frames": 550, "state": 7, "video": 70})
        self.assertEqual((partitions, tasks), (3, 4))
        rows = {row["object"]: row for row in droid_objects.table(size, partitions, tasks, runs=2)}
        self.assertEqual(rows["frames"]["times source"], round(550 / 150, 5))
        self.assertEqual(rows["frames"]["MB per partition"], round(550 / 3 / 1e6, 3))


class DroidPipelineFigure(unittest.TestCase):
    def setUp(self):
        self.slide = figures.droid_pipeline(blank_deck())
        self.megabytes = {
            row["object"]: float(row["MB per partition"]) for row in figures.read_csv("t10_droid_objects.csv")
        }

    def filled(self, colour):
        return [
            shape
            for shape in self.slide.shapes
            if shape.shape_type == 1 and shape.fill.type == 1 and str(shape.fill.fore_color.rgb) == colour
        ]

    def test_box_heights_are_proportional_to_bytes(self):
        heights = sorted(box.height for box in self.filled(figures.PANEL))
        sizes = sorted(self.megabytes[name] for name in figures.PIPELINE_DRAWN_TO_SCALE)
        self.assertEqual(len(heights), 3)
        for height, size in zip(heights, sizes):
            self.assertAlmostEqual(height / Inches(1) / size, figures.PIPELINE_INCH_PER_MB, places=4)

    def test_one_function_per_task_and_one_thin_bar_per_small_object(self):
        self.assertEqual(texts(self.slide).count(figures.FUNCTION_GLYPH), 4)
        self.assertEqual(len(self.filled(figures.CS_TINT)), 4)
        small = [name for name in self.megabytes if name not in figures.PIPELINE_DRAWN_TO_SCALE]
        self.assertEqual(len(self.filled(figures.PIPELINE_SLIVER_FILL)), len(small))

    def test_arrows_account_for_the_network_traffic_the_baseline_measured(self):
        rows = {row["object"]: row for row in figures.read_csv("t10_droid_objects.csv")}
        measured = float(figures.read_csv("t2_droid_totals.csv")[0]["heft GB moved"])
        self.assertAlmostEqual(figures.pipeline_traffic_gb(rows) / measured, 1, places=3)

    def test_labels_show_the_measured_megabytes(self):
        shown = texts(self.slide)
        for name in figures.PIPELINE_DRAWN_TO_SCALE:
            self.assertIn(f"{self.megabytes[name]:.0f} MB", shown)


class Figures(unittest.TestCase):
    def test_every_figure_is_one_slide_on_the_page_and_clear_of_the_heading(self):
        for figure in figures.FIGURES:
            presentation = blank_deck()
            slide = figure(presentation)
            self.assertEqual(len(presentation.slides), 1, figure.__name__)
            for shape in slide.shapes:
                where = f"{figure.__name__}: {shape.name}"
                self.assertGreaterEqual(shape.top, HEADING_BOTTOM, where)
                self.assertGreaterEqual(shape.left, 0, where)
                self.assertLessEqual(shape.left + shape.width, presentation.slide_width, where)
                self.assertLessEqual(shape.top + shape.height, presentation.slide_height, where)

    def test_blind_plan_shows_the_plan_and_the_measurement_of_plain_heft(self):
        row = next(row for row in figures.read_csv("t11_heft_plan.csv") if row["policy"] == "heft")
        shown = texts(figures.blind_plan(blank_deck()))
        moved = row["tasks off node 0"]
        self.assertIn(f"{row['planned s']} s", shown)
        self.assertIn(f"{row['measured s']} s", shown)
        self.assertIn(f"{moved} tasks moved off the busy node", shown)
        self.assertIn(f"{moved} × {row['shard s on the link']} s on one link = {row['link s for the moves']} s", shown)
        link_seconds = int(moved) * float(row["shard s on the link"])
        self.assertAlmostEqual(link_seconds, float(row["link s for the moves"]), delta=0.2)

    def test_findings_show_one_measured_number_per_challenge(self):
        droid = figures.read_csv("t2_droid_totals.csv")[0]
        slots = figures.read_csv("t1_slots.csv")
        measured = {row["policy"]: row["measured s"] for row in figures.read_csv("t11_heft_plan.csv")}
        lightest = min(slots, key=lambda row: int(row["iterations"]))
        heaviest = max(slots, key=lambda row: int(row["iterations"]))
        shown = texts(figures.findings(blank_deck()))
        self.assertIn(f"{float(droid['speedup']):.2f}×", shown)
        speedups = [float(row["cs2 over disagg2 median"]) for row in (lightest, heaviest)]
        self.assertIn(f"{speedups[0]:.2f}× → {speedups[1]:.2f}×", shown)
        self.assertIn(f"{measured['heft']} s → {measured['heft-cc']} s", shown)
        self.assertIn(f"faster on\n{float(droid['source GB']):.0f} GB of DROID", shown)
        for label in figures.FINDINGS_LABELS:
            self.assertIn(label, shown)


class AddSlide(unittest.TestCase):
    def setUp(self):
        self.presentation = deck_without_notes_placeholders()
        earlier = self.presentation.slides.add_slide(self.presentation.slide_layouts[5])
        earlier.shapes.title.text = "an earlier slide"
        self.added = add_slide.add_figure_slide(self.presentation, figures.droid_pipeline, "His title", "")
        self.new = self.presentation.slides[1]

    def test_earlier_slides_are_left_alone_and_the_new_slide_is_returned(self):
        self.assertEqual(len(self.presentation.slides), 2)
        self.assertEqual(texts(self.presentation.slides[0]), ["an earlier slide"])
        self.assertEqual([slide.slide_id for slide in self.added], [self.new.slide_id])

    def test_title_is_set_in_his_heading_style(self):
        title = next(
            shape for shape in self.new.shapes if shape.has_text_frame and shape.text_frame.text == "His title"
        )
        font = title.text_frame.paragraphs[0].runs[0].font
        self.assertEqual((font.name, font.size.pt), (figures.MEDIUM, add_slide.TITLE_PT))
        self.assertEqual(title.top, Inches(add_slide.TITLE_BOX[1]))

    def test_empty_subtitle_keeps_its_style_for_typed_text(self):
        subtitle = next(shape for shape in self.new.shapes if shape.top == Inches(add_slide.SUBTITLE_BOX[1]))
        paragraph = subtitle.text_frame.paragraphs[0]
        self.assertEqual(paragraph.runs, ())
        style = paragraph._p.find(figures.qn("a:endParaRPr"))
        self.assertEqual(style.get("sz"), str(add_slide.SUBTITLE_PT * 100))
        self.assertEqual(style.find(figures.qn("a:latin")).get("typeface"), figures.SANS)

    def test_notes_are_written_when_the_notes_master_has_no_placeholders(self):
        self.assertIn("t10_droid_objects.csv", notes(self.new))

    def test_unknown_figure_is_refused_by_name(self):
        with self.assertRaisesRegex(ValueError, "unknown figure 'no_such_figure'"):
            add_slide.figure_named("no_such_figure")


class BuiltDeck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(build_deck.SPEC.read_text(encoding="utf-8"))
        cls.demo_1_digest = digest(build_deck.DEMO_1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deck.pptx"
            build_deck.build(cls.spec, path)
            cls.deck = Presentation(path)
        cls.demo_1 = Presentation(build_deck.DEMO_1)
        cls.pairs = list(zip(cls.spec, cls.deck.slides))

    def test_deck_has_one_slide_per_spec_entry(self):
        self.assertEqual(len(self.deck.slides), len(self.spec))
        self.assertTrue(any("demo1" in entry for entry in self.spec))
        self.assertTrue(any("figure" in entry for entry in self.spec))

    def test_demo_1_slides_are_kept_as_they_were_in_the_spec_position(self):
        for entry, slide in self.pairs:
            if "demo1" in entry:
                original = self.demo_1.slides[entry["demo1"] - 1]
                self.assertEqual(texts(slide), texts(original))
                self.assertEqual(len(slide.shapes), len(original.shapes))
                self.assertEqual(notes(slide), notes(original))

    def test_demo_1_file_is_not_modified(self):
        self.assertEqual(digest(build_deck.DEMO_1), self.demo_1_digest)

    def test_figure_slides_carry_the_spec_heading_in_the_spec_position(self):
        for entry, slide in self.pairs:
            if "figure" in entry:
                heading = [
                    shape.text_frame.text
                    for shape in slide.shapes
                    if shape.has_text_frame and shape.top < HEADING_BOTTOM
                ]
                self.assertEqual(heading, [entry["title"], entry["subtitle"]], entry["figure"])

    def test_figure_notes_are_what_to_say_then_the_time_then_the_source(self):
        for entry, slide in self.pairs:
            if "figure" in entry:
                source = notes(figures_slide(entry["figure"]))
                said, time, rest = notes(slide).split("\n\n", 2)
                self.assertEqual(said, entry["say"])
                self.assertEqual(time, f"Suggested time: {entry['seconds']} seconds.")
                self.assertEqual(rest, source)


def figures_slide(name):
    return add_slide.figure_named(name)(blank_deck())


if __name__ == "__main__":
    unittest.main()
