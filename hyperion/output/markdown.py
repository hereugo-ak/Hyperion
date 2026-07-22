"""
HYPERION Markdown Export — renders FinalReport as markdown for TUI display.

This is NOT a generic "convert to markdown" wrapper. It renders the
FinalReport Pydantic model into a structured markdown document that
the TUI can display using Rich Markdown rendering.

The markdown output mirrors the PDF structure:
- Cover (title, subtitle, client, date)
- Table of contents
- Executive summary
- Sections (with findings, charts, tables)
- Risk section
- Methodology
- Appendix
- Back cover (wordmark, tagline)

Architecture reference: §8.2 — "Deliverable View: Rendered markdown of
the final report (Rich Markdown widget). Export button (save as PDF,
save as markdown)."

§8.1 — "Markdown: Rich Markdown rendering (headers, bold, lists, code
blocks)" — this is the HYPERION advantage over Hermes Agents.

Used by: TUI Deliverable View, export to .md file (§8.2)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class MarkdownExportResult:
    """Result of exporting a report as markdown."""

    markdown: str = ""
    char_count: int = 0
    line_count: int = 0
    section_count: int = 0
    success: bool = False
    error: str = ""
    file_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "markdown": self.markdown,
            "char_count": self.char_count,
            "line_count": self.line_count,
            "section_count": self.section_count,
            "success": self.success,
            "error": self.error,
            "file_path": self.file_path,
        }


class MarkdownExporter:
    """Renders FinalReport as structured markdown for TUI display.

    Produces markdown that mirrors the PDF structure, optimized for
    Rich Markdown rendering in the TUI.

    Usage:
        exporter = MarkdownExporter(settings=settings)
        result = exporter.export(report_data=final_report_dict)
        if result.success:
            print(f"Exported {result.char_count} chars, {result.section_count} sections")
    """

    WORDMARK = "HYPERION"
    TAGLINE = "many minds. one reading."

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings

    def _format_currency(self, value: float | None, currency: str = "$") -> str:
        """Format a number as currency."""
        if value is None:
            return "N/A"
        if abs(value) >= 1_000_000_000:
            return f"{currency}{value / 1_000_000_000:.1f}B"
        elif abs(value) >= 1_000_000:
            return f"{currency}{value / 1_000_000:.1f}M"
        elif abs(value) >= 1_000:
            return f"{currency}{value / 1_000:.1f}K"
        else:
            return f"{currency}{value:.2f}"

    def _format_percent(self, value: float | None, decimals: int = 1) -> str:
        """Format a number as percentage."""
        if value is None:
            return "N/A"
        return f"{value:.{decimals}f}%"

    def _render_cover(self, report: dict[str, Any]) -> str:
        """Render the cover page as markdown."""
        title = report.get("title", "Untitled Report")
        subtitle = report.get("subtitle", "")
        client = report.get("client", "")
        date = report.get("date", datetime.now().strftime("%B %d, %Y"))

        lines = [
            f"# {self.WORDMARK}",
            f"### *{self.TAGLINE}*",
            "",
            "---",
            "",
            f"# {title}",
            "",
        ]
        if subtitle:
            lines.append(f"### {subtitle}")
            lines.append("")
        if client:
            lines.append(f"**Prepared for:** {client}")
            lines.append("")
        lines.append(f"**Date:** {date}")
        lines.append("")
        lines.append("---")
        lines.append("")

        return "\n".join(lines)

    def _render_toc(self, sections: list[dict[str, Any]]) -> str:
        """Render table of contents."""
        lines = ["## Table of Contents", ""]

        for i, section in enumerate(sections, 1):
            title = section.get("title", f"Section {i}")
            lines.append(f"{i}. [{title}](#{i}-{title.lower().replace(' ', '-')})")

        lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    def _render_executive_summary(self, report: dict[str, Any]) -> str:
        """Render the executive summary."""
        summary = report.get("executive_summary", {})
        if not summary:
            return ""

        # Handle case where LLM returns summary as a string instead of dict
        if isinstance(summary, str):
            return f"## Executive Summary\n\n{summary}\n"

        lines = ["## Executive Summary", ""]

        # Key findings
        key_findings = summary.get("key_findings", [])
        if key_findings:
            lines.append("### Key Findings")
            lines.append("")
            for finding in key_findings:
                lines.append(f"- {finding}")
            lines.append("")

        # Recommendations
        recommendations = summary.get("recommendations", [])
        if recommendations:
            lines.append("### Recommendations")
            lines.append("")
            for rec in recommendations:
                lines.append(f"- {rec}")
            lines.append("")

        # Key metrics
        metrics = summary.get("key_metrics", {})
        if metrics:
            lines.append("### Key Metrics")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            for key, value in metrics.items():
                lines.append(f"| {key} | {value} |")
            lines.append("")

        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    def _render_section(self, section: dict[str, Any], index: int) -> str:
        """Render a single report section."""
        title = section.get("title", f"Section {index}")
        content = section.get("content", "")

        lines = [f"## {index}. {title}", ""]

        # Section content (markdown)
        if content:
            lines.append(content)
            lines.append("")

        # Key findings in this section
        findings = section.get("findings", [])
        if findings:
            lines.append("### Key Findings")
            lines.append("")
            for finding in findings:
                if isinstance(finding, dict):
                    lines.append(f"- **{finding.get('title', '')}**: {finding.get('description', '')}")
                    if finding.get("confidence"):
                        lines.append(f"  - *Confidence: {finding.get('confidence', '')}*")
                    if finding.get("sources"):
                        lines.append(f"  - *Sources: {', '.join(finding['sources'][:3])}*")
                else:
                    lines.append(f"- {finding}")
            lines.append("")

        # Charts (reference image paths)
        charts = section.get("charts", [])
        if charts:
            lines.append("### Charts")
            lines.append("")
            for chart in charts:
                if isinstance(chart, dict):
                    caption = chart.get("caption", chart.get("title", ""))
                    path = chart.get("image_path", "")
                    source = chart.get("source", "")
                    if path:
                        lines.append(f"![{caption}]({path})")
                    if caption:
                        lines.append(f"*{caption}*")
                    if source:
                        lines.append(f"*Source: {source}*")
                    lines.append("")

        # Tables
        tables = section.get("tables", [])
        if tables:
            lines.append("### Data Tables")
            lines.append("")
            for table in tables:
                if isinstance(table, dict):
                    headers = table.get("headers", [])
                    rows = table.get("rows", [])
                    if headers:
                        lines.append("| " + " | ".join(headers) + " |")
                        lines.append("|" + "|".join(["---"] * len(headers)) + "|")
                        for row in rows:
                            lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
                        lines.append("")

        # Images
        images = section.get("images", [])
        if images:
            for img in images:
                if isinstance(img, dict):
                    caption = img.get("caption", "")
                    path = img.get("local_path", img.get("image_path", ""))
                    photographer = img.get("photographer", "")
                    if path:
                        lines.append(f"![{caption}]({path})")
                    if caption:
                        lines.append(f"*{caption}*")
                    if photographer:
                        lines.append(f"*Photo: {photographer} via Unsplash*")
                    lines.append("")

        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    def _render_risks(self, report: dict[str, Any]) -> str:
        """Render the risk section."""
        risks = report.get("risks", [])
        if not risks:
            return ""

        lines = ["## Risk Analysis", ""]

        # Risk matrix
        lines.append("### Top Risks")
        lines.append("")
        lines.append("| # | Risk | Severity | Probability | Mitigation |")
        lines.append("|---|------|----------|-------------|------------|")

        for i, risk in enumerate(risks[:10], 1):
            if isinstance(risk, dict):
                name = risk.get("name", risk.get("title", ""))
                severity = risk.get("severity", "")
                probability = risk.get("probability", "")
                mitigation = risk.get("mitigation", risk.get("recommendation", ""))
                lines.append(f"| {i} | {name} | {severity} | {probability} | {mitigation} |")

        lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    def _render_methodology(self, report: dict[str, Any]) -> str:
        """Render the methodology section."""
        methodology = report.get("methodology", {})
        if not methodology:
            return ""

        lines = ["## Methodology", ""]

        # Agents used
        agents = methodology.get("agents_used", [])
        if agents:
            lines.append("### Agents Deployed")
            lines.append("")
            for agent in agents:
                if isinstance(agent, dict):
                    lines.append(f"- **{agent.get('name', '')}** — {agent.get('role', '')}")
                else:
                    lines.append(f"- {agent}")
            lines.append("")

        # Data sources
        sources = methodology.get("data_sources", [])
        if sources:
            lines.append("### Data Sources")
            lines.append("")
            for source in sources:
                if isinstance(source, dict):
                    lines.append(f"- {source.get('name', '')} — *{source.get('type', '')}*")
                else:
                    lines.append(f"- {source}")
            lines.append("")

        # Tools used
        tools = methodology.get("tools_used", [])
        if tools:
            lines.append("### Tools Used")
            lines.append("")
            for tool in tools:
                lines.append(f"- {tool}")
            lines.append("")

        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    def _render_appendix(self, report: dict[str, Any]) -> str:
        """Render the appendix."""
        appendix = report.get("appendix", {})
        if not appendix:
            return ""

        lines = ["## Appendix", ""]

        # Sources
        sources = appendix.get("sources", [])
        if sources:
            lines.append("### Sources")
            lines.append("")
            for i, source in enumerate(sources, 1):
                if isinstance(source, dict):
                    title = source.get("title", "")
                    url = source.get("url", "")
                    credibility = source.get("credibility", "")
                    lines.append(f"{i}. [{title}]({url}) — *Credibility: {credibility}*")
                else:
                    lines.append(f"{i}. {source}")
            lines.append("")

        # Glossary
        glossary = appendix.get("glossary", {})
        if glossary:
            lines.append("### Glossary")
            lines.append("")
            for term, definition in glossary.items():
                lines.append(f"- **{term}**: {definition}")
            lines.append("")

        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    def _render_back_cover(self) -> str:
        """Render the back cover."""
        lines = [
            f"# {self.WORDMARK}",
            f"### *{self.TAGLINE}*",
            "",
            "---",
            "",
            f"*Generated by HYPERION on {datetime.now().strftime('%B %d, %Y at %H:%M')}*",
            "",
        ]
        return "\n".join(lines)

    def export(self, report_data: dict[str, Any]) -> MarkdownExportResult:
        """Export a FinalReport as structured markdown.

        Args:
            report_data: Dictionary containing the FinalReport data

        Returns:
            MarkdownExportResult with the rendered markdown.
        """
        try:
            sections_data = report_data.get("sections", [])

            # Build the full markdown document
            parts: list[str] = []

            # Cover
            parts.append(self._render_cover(report_data))

            # Table of contents
            parts.append(self._render_toc(sections_data))

            # Executive summary
            parts.append(self._render_executive_summary(report_data))

            # Sections
            for i, section in enumerate(sections_data, 1):
                parts.append(self._render_section(section, i))

            # Risk analysis
            parts.append(self._render_risks(report_data))

            # Methodology
            parts.append(self._render_methodology(report_data))

            # Appendix
            parts.append(self._render_appendix(report_data))

            # Back cover
            parts.append(self._render_back_cover())

            markdown = "\n".join(parts)

            return MarkdownExportResult(
                markdown=markdown,
                char_count=len(markdown),
                line_count=markdown.count("\n") + 1,
                section_count=len(sections_data),
                success=True,
            )

        except (ValueError, KeyError, TypeError, AttributeError) as e:
            return MarkdownExportResult(error=str(e))

    def export_to_file(
        self,
        report_data: dict[str, Any],
        file_path: str = "",
    ) -> MarkdownExportResult:
        """Export a FinalReport as markdown and save to file.

        Args:
            report_data: Dictionary containing the FinalReport data
            file_path: Path to save the markdown file. If empty, auto-generated.

        Returns:
            MarkdownExportResult with the file path.
        """
        result = self.export(report_data)
        if not result.success:
            return result

        if not file_path:
            from pathlib import Path

            reports_dir = Path("reports")
            if self.settings:
                reports_dir = Path(getattr(self.settings, "reports_dir", "reports"))
            reports_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_path = str(reports_dir / f"hyperion_report_{timestamp}.md")

        try:
            from pathlib import Path

            Path(file_path).write_text(result.markdown, encoding="utf-8")
            result.file_path = file_path
            return result
        except OSError as e:
            result.error = str(e)
            return result

    async def close(self) -> None:
        """Close any open resources."""
        pass

    async def __aenter__(self) -> MarkdownExporter:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
