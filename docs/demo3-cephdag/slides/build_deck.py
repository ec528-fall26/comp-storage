"""Build demo-3-aakash.pptx from deck.json, on top of Aakash's Demo 1 deck.

deck.json lists the slides in order: {"demo1": N} keeps slide N of demo-1.pptx as it is;
{"figure", "title", "subtitle", "seconds", "say"} appends that figure from make_slide_figures
under the heading, with speaker notes made of what to say, the suggested time and the
figure's data source. Demo 1 slides the spec does not name are dropped.

    $PY build_deck.py [OUTPUT.pptx]
"""

import json
import sys
from pathlib import Path

from pptx import Presentation

import add_slide

HERE = Path(__file__).resolve().parent
DEMO_1 = HERE / "demo-1.pptx"
SPEC = HERE / "deck.json"
OUTPUT = HERE / "demo-3-aakash.pptx"


def speaker_notes(entry, source):
    return f"{entry['say']}\n\nSuggested time: {entry['seconds']} seconds.\n\n{source}"


def add_figure_entry(presentation, entry):
    figure = add_slide.figure_named(entry["figure"])
    new_slides = add_slide.add_figure_slide(presentation, figure, entry["title"], entry["subtitle"])
    for slide in new_slides:
        frame = slide.notes_slide.notes_text_frame
        frame.text = speaker_notes(entry, frame.text)
    return len(new_slides)


def build(spec, output):
    presentation = Presentation(DEMO_1)
    slide_ids = presentation.slides._sldIdLst
    demo_1_ids = list(slide_ids)
    ordered = []
    for entry in spec:
        if "demo1" in entry:
            ordered.append(demo_1_ids[entry["demo1"] - 1])
        else:
            count = add_figure_entry(presentation, entry)
            ordered.extend(list(slide_ids)[-count:])
    for slide_id in list(slide_ids):
        slide_ids.remove(slide_id)
        if slide_id not in ordered:
            presentation.part.drop_rel(slide_id.rId)
    for slide_id in ordered:
        slide_ids.append(slide_id)
    presentation.save(output)
    return presentation


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    presentation = build(spec, output)
    print(f"wrote {output}: {len(presentation.slides)} slides")


if __name__ == "__main__":
    main()
