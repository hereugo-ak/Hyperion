"""Render the production template and report two-column typographic metrics.

Runs as a SUBPROCESS on purpose, and in two *sequential* phases. This is a
memory requirement, not a style preference. Measured on this 985 MB host:

    baseline interpreter                       10 MB
    + weasyprint/hyperion imports             113 MB
    + jinja render (2.3 MB HTML)              138 MB
    + WeasyPrint write_pdf                    245 MB   (+107 MB)
    + one fitz get_text("dict") sweep         414 MB   (+169 MB)

Neither library returns that memory to the OS. Doing both phases in one
interpreter costs ~427 MB peak; doing them as two sequential children costs
~300 MB, because the render's 245 MB is reclaimed at phase-1 exit before the
measure allocates anything.

Alternatives that were measured and rejected:
  * streaming pages + gc.collect()  — no effect, fitz does not return it
  * reopening the doc per page       — 300 MB, same as the plain sweep
  * fitz "words" mode               — 7 MB, but carries no font size, so the
                                      9-11 pt body filter is impossible
  * fitz get_texttrace()            — 8 MB, but groups by span not by line, so
                                      it reported median 197 / p90 1854 chars
                                      against a true median of 54. Cheap and
                                      wrong is worse than expensive and right.

So "dict" mode stays, and process boundaries do the reclaiming.

Usage:
    python3 tools/measure_two_column.py [out.pdf]        # both phases, JSON out
    python3 tools/measure_two_column.py --render out.pdf # phase 1 only
    python3 tools/measure_two_column.py --measure out.pdf# phase 2 only, JSON out
"""

from __future__ import annotations

import json
import pathlib
import statistics
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

# Body text measure band, in points. Matches tools/audit_render_probe.py.
BODY_PT_MIN = 9.0
BODY_PT_MAX = 11.0
# Minimum characters for a line to count as prose rather than a label/heading.
MIN_PROSE_CHARS = 25
# A4 midpoint in points (595.2 / 2) — left column vs right column.
COLUMN_SPLIT_PT = 297.6


def render(out: pathlib.Path) -> None:
    """Phase 1: render the production template path to a real PDF."""
    import audit_render_probe
    from jinja2 import BaseLoader, Environment
    from weasyprint import HTML

    # Import the templates from the PRODUCTION module, never from the test
    # module. Importing tests.test_two_column_layout would drag pytest and its
    # plugins into this child, and with the parent pytest already holding
    # ~150 MB that pushed the child over the ceiling: it was OOM-killed and
    # surfaced as an opaque rc=-9. The child must stay minimal to stay alive.
    from hyperion.agents.delivery.presentation_designer import (
        CSS_TEMPLATE,
        HTML_TEMPLATE,
        PDF_PALETTE,
    )
    from hyperion.output.render import TemplateRenderer

    payload = audit_render_probe.build_payload()
    env = Environment(loader=BaseLoader(), autoescape=True)
    env.filters["md_to_html"] = TemplateRenderer()._markdown_to_html
    env.filters["clean_dict_repr"] = lambda v: str(v) if v else ""
    html = env.from_string(HTML_TEMPLATE).render(
        css_content=CSS_TEMPLATE,
        palette=PDF_PALETTE,
        risk_analysis_html="<p>No risk analysis available.</p>",
        appendix_sources_html="<p>No sources.</p>",
        **payload,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(ROOT)).write_pdf(str(out))


def measure(pdf: pathlib.Path) -> dict[str, object]:
    """Phase 2: measure column bands and line length on the rendered PDF."""
    import fitz

    line_chars: list[int] = []
    bands: set[int] = set()
    with fitz.open(str(pdf)) as doc:
        pages = len(doc)
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                if block["type"] != 0:
                    continue
                for line in block["lines"]:
                    spans = [
                        s for s in line["spans"] if BODY_PT_MIN <= s["size"] <= BODY_PT_MAX
                    ]
                    text = "".join(s["text"] for s in spans).strip()
                    if len(text) < MIN_PROSE_CHARS:
                        continue
                    line_chars.append(len(text))
                    x0 = min(s["bbox"][0] for s in spans)
                    bands.add(0 if x0 < COLUMN_SPLIT_PT else 1)

    srt = sorted(line_chars)
    return {
        "pdf": str(pdf),
        "pages": pages,
        "line_count": len(line_chars),
        "bands": len(bands),
        "median": statistics.median(line_chars) if line_chars else 0,
        "p90": srt[int(len(srt) * 0.9)] if srt else 0,
    }


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--render":
        render(pathlib.Path(argv[1]))
        return 0
    if argv and argv[0] == "--measure":
        json.dump(measure(pathlib.Path(argv[1])), sys.stdout)
        return 0

    # Default: drive both phases as separate children so their peaks never
    # overlap. ~300 MB instead of ~427 MB.
    out = pathlib.Path(argv[0]) if argv else pathlib.Path("/tmp/two_col.pdf")
    me = str(pathlib.Path(__file__).resolve())
    r = subprocess.run(  # noqa: S603
        [sys.executable, me, "--render", str(out)], capture_output=True, text=True, check=False
    )
    if r.returncode != 0:
        sys.stderr.write(f"render phase failed rc={r.returncode}\n{r.stderr[-4000:]}")
        return 1
    m = subprocess.run(  # noqa: S603
        [sys.executable, me, "--measure", str(out)], capture_output=True, text=True, check=False
    )
    if m.returncode != 0:
        sys.stderr.write(f"measure phase failed rc={m.returncode}\n{m.stderr[-4000:]}")
        return 1
    sys.stdout.write(m.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
