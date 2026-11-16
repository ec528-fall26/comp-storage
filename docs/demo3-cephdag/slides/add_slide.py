"""Append one figure to a deck, under a heading in the style of Aakash's Demo 1 slides.

FIGURE is the name of a function in make_slide_figures.FIGURES. The words of the heading
are his: an omitted subtitle leaves an empty box in the subtitle style for him to fill.

    $PY add_slide.py DECK.pptx FIGURE [TITLE [SUBTITLE]]
"""

import copy
import sys

from pptx import Presentation
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn

import make_slide_figures as figures

TITLE_BOX = (0.67, 0.43, 12.0, 0.65)
SUBTITLE_BOX = (0.68, 1.19, 11.94, 0.35)
TITLE_PT, SUBTITLE_PT = 34, 17


def figure_named(name):
    available = {figure.__name__: figure for figure in figures.FIGURES}
    if name not in available:
        raise ValueError(f"unknown figure {name!r}; choose one of {', '.join(available)}")
    return available[name]


def keep_style_when_empty(box):
    """Move the empty run's font to the paragraph end, where PowerPoint reads it for typed text."""
    paragraph = box.text_frame.paragraphs[0]._p
    run = paragraph.find(qn("a:r"))
    style = copy.deepcopy(run.find(qn("a:rPr")))
    style.tag = qn("a:endParaRPr")
    paragraph.remove(run)
    paragraph.append(style)


def add_heading_line(slide, text, box, size, colour, font):
    line = figures.add_text(slide, text, *box, size, colour, font, PP_ALIGN.LEFT, MSO_ANCHOR.TOP)
    if not text:
        keep_style_when_empty(line)


def add_figure_slide(presentation, figure, title, subtitle):
    first_new = len(presentation.slides)
    figure(presentation)
    new_slides = list(presentation.slides)[first_new:]
    for slide in new_slides:
        add_heading_line(slide, title, TITLE_BOX, TITLE_PT, figures.INK, figures.MEDIUM)
        add_heading_line(slide, subtitle, SUBTITLE_BOX, SUBTITLE_PT, figures.SECONDARY, figures.SANS)
    return new_slides


def main():
    deck, name = sys.argv[1:3]
    title, subtitle = (sys.argv[3:5] + ["", ""])[:2]
    try:
        figure = figure_named(name)
    except ValueError as error:
        sys.exit(str(error))
    presentation = Presentation(deck)
    add_figure_slide(presentation, figure, title, subtitle)
    presentation.save(deck)
    print(f"{deck}: {len(presentation.slides)} slides")


if __name__ == "__main__":
    main()
