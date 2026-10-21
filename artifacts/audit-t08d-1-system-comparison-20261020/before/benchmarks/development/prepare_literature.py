"""Render a frozen JATS section without silently dropping image equations.

No network, OCR, or mathematical transcription is performed. The selected
section and its figure caption are development inputs, not a whole-paper test.
"""
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
CASE = ROOT / "benchmarks/inputs/literature/gonze2013"
XLINK = "{http://www.w3.org/1999/xlink}href"


def render(node):
    if node.tag in {"graphic", "inline-graphic"}:
        return f" [IMAGE_FORMULA: {node.get(XLINK, 'unknown')}] "
    # Bibliographic IDs and display metadata are not scientific prose.
    if node.tag in {"object-id", "label"}:
        return ""
    parts = [node.text or ""]
    for child in node:
        parts.extend((render(child), child.tail or ""))
    return "".join(parts)


def prepare():
    root = ET.parse(CASE / "article.xml").getroot()
    section = next(n for n in root.iter("sec") if n.get("id") == "s2a")
    # Figure 2 is already nested in this section in the publisher's XML.
    text = " ".join(render(section).split())
    header = (
        "The Goodwin Model: Behind the Hill Function — Gonze & Abou-Jaoude (2013)\n"
        "DOI: 10.1371/journal.pone.0069573; license: CC BY.\n"
        "Input scope: Results, Limit-cycle Oscillations in the Goodwin Model, "
        "including Figure 2 caption.\n"
        "Parser: deterministic JATS text; formula images remain explicit placeholders.\n"
        "Task: reconstruct equations (1)-(3) and Figure 2B parameter values. "
        "Use the authors' symbol names. Missing initial conditions or inaccessible "
        "image content must remain unknown; request researcher input.\n\n"
    )
    (CASE / "raw-text.txt").write_text(header + text + "\n", encoding="utf-8")


if __name__ == "__main__":
    prepare()
