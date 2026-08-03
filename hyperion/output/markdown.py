"""
HYPERION Markdown Export, renders FinalReport as markdown for TUI display.

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

Architecture reference: §8.2"Deliverable View: Rendered markdown of
the final report (Rich Markdown widget). Export button (save as PDF,
save as markdown)."

§8.1"Markdown: Rich Markdown rendering (headers, bold, lists, code
blocks)", this is the HYPERION advantage over Hermes Agents.

Used by: TUI Deliverable View, export to .md file (§8.2)
"""

from __future__ import annotations

from dataclasses import dataclass
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
        """Render the cover page as markdown.

        D18 fix: uses FinalReport schema keys (question, engagement_id, generated_at)
        instead of non-existent title/subtitle/client/date.
        """
        title = report.get("question", "Untitled Report")
        engagement_id = report.get("engagement_id", "")
        generated_at = report.get("generated_at", "")
        if generated_at:
            if isinstance(generated_at, str):
                date_str = generated_at[:10]
            else:
                date_str = str(generated_at)[:10]
        else:
            date_str = datetime.now().strftime("%B %d, %Y")

        lines = [
            f"# {self.WORDMARK}",
            f"### *{self.TAGLINE}*",
            "",
            "---",
            "",
            f"# {title}",
            "",
        ]
        if engagement_id:
            lines.append(f"**Engagement:** {engagement_id}")
            lines.append("")
        lines.append(f"**Date:** {date_str}")
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
        """Render the executive summary.

        D18 fix: executive_summary is a string in FinalReport, not a dict.
        key_findings is a list of KeyFinding objects.
        recommendation and recommendation_rationale are top-level fields.
        """
        lines = ["## Executive Summary", ""]

        # Recommendation
        recommendation = report.get("recommendation", "")
        if recommendation:
            rec_str = recommendation if isinstance(recommendation, str) else str(recommendation)
            lines.append(f"**Recommendation:** {rec_str.replace('_', ' ').title()}")
            lines.append("")

        rationale = report.get("recommendation_rationale", "")
        if rationale:
            lines.append(f"{rationale}")
            lines.append("")

        # Executive summary text (it's a string, not a dict)
        summary = report.get("executive_summary", "")
        if summary and isinstance(summary, str):
            lines.append(summary)
            lines.append("")

        # Key findings (list of KeyFinding objects or dicts)
        key_findings = report.get("key_findings", [])
        if key_findings:
            lines.append("### Key Findings")
            lines.append("")
            for finding in key_findings:
                if isinstance(finding, dict):
                    title = finding.get("title", "")
                    content = finding.get("content", "")
                    confidence = finding.get("confidence", "")
                    lines.append(f"- **{title}**: {content[:200]}")
                    if confidence:
                        conf_str = confidence if isinstance(confidence, str) else str(confidence)
                        lines.append(f"  - *Confidence: {conf_str}*")
                else:
                    lines.append(f"- {finding}")
            lines.append("")

        # Critical assumptions
        assumptions = report.get("critical_assumptions", [])
        if assumptions:
            lines.append("### Critical Assumptions")
            lines.append("")
            for assumption in assumptions:
                lines.append(f"- {assumption}")
            lines.append("")

        # Confidence
        confidence = report.get("confidence", "")
        if confidence:
            conf_str = confidence if isinstance(confidence, str) else str(confidence)
            lines.append(f"*Overall Confidence: {conf_str}*")
            lines.append("")

        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    def _render_section(self, section: dict[str, Any], index: int) -> str:
        """Render a single report section.

        D18 fix: uses 'body' instead of 'content', and findings are KeyFinding
        objects with 'title'/'content'/'confidence'/'sources' fields.
        """
        title = section.get("title", f"Section {index}")
        body = section.get("body", section.get("content", ""))
        key_insight = section.get("key_insight", "")

        lines = [f"## {index}. {title}", ""]

        # Key insight box
        if key_insight:
            lines.append(f"> **Key Insight:** {key_insight}")
            lines.append("")

        # Section body (markdown)
        if body:
            lines.append(body)
            lines.append("")

        # Key findings in this section (KeyFinding objects or dicts)
        findings = section.get("findings", [])
        if findings:
            lines.append("### Key Findings")
            lines.append("")
            for finding in findings:
                if isinstance(finding, dict):
                    f_title = finding.get("title", "")
                    raw_content = finding.get("content", finding.get("description", ""))
                    f_content = raw_content if isinstance(raw_content, str) else str(raw_content)
                    f_conf = finding.get("confidence", "")
                    lines.append(f"- **{f_title}**: {f_content[:200]}")
                    if f_conf:
                        conf_str = f_conf if isinstance(f_conf, str) else str(f_conf)
                        lines.append(f"  - *Confidence: {conf_str}*")
                    f_sources = finding.get("sources", [])
                    if f_sources:
                        src_titles: list[str] = []
                        for source in f_sources[:3]:
                            if isinstance(source, dict):
                                raw_title = source.get("title", source.get("url", ""))
                                src_titles.append(
                                    raw_title if isinstance(raw_title, str) else str(raw_title)
                                )
                            else:
                                src_titles.append(str(source))
                        if src_titles:
                            lines.append(f"  - *Sources: {', '.join(src_titles)}*")
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
                    path = chart.get("image_path", chart.get("path", ""))
                    source = chart.get("source", "")
                    if path:
                        lines.append(f"![{caption}]({path})")
                    if caption:
                        lines.append(f"*{caption}*")
                    if source:
                        lines.append(f"*Source: {source}*")
                    lines.append("")
                elif isinstance(chart, str):
                    lines.append(f"![Chart]({chart})")
                    lines.append("")

        # Images
        images = section.get("images", [])
        if images:
            for img in images:
                if isinstance(img, dict):
                    caption = img.get("caption", "")
                    path = img.get("local_path", img.get("image_path", img.get("path", "")))
                    photographer = img.get("photographer", "")
                    if path:
                        lines.append(f"![{caption}]({path})")
                    if caption:
                        lines.append(f"*{caption}*")
                    if photographer:
                        lines.append(f"*Photo: {photographer}*")
                    lines.append("")
                elif isinstance(img, str):
                    lines.append(f"![Image]({img})")
                    lines.append("")

        # Implications
        implications = section.get("implications", "")
        if implications:
            lines.append(f"> **So What?** {implications}")
            lines.append("")

        # Sources
        sources = section.get("sources", [])
        if sources:
            lines.append("### Sources")
            lines.append("")
            for src in sources:
                if isinstance(src, dict):
                    s_title = src.get("title", "")
                    s_url = src.get("url", "")
                    s_cred = src.get("credibility", "")
                    if s_url:
                        lines.append(f"- [{s_title}]({s_url})" + (f", *{s_cred}*" if s_cred else ""))
                    else:
                        lines.append(f"- {s_title}")
                else:
                    lines.append(f"- {src}")
            lines.append("")

        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    def _render_risks(self, report: dict[str, Any]) -> str:
        """Render the risk section.

        D18 fix: uses 'risk_analysis' (RiskAnalysis model dict) instead of
        non-existent 'risks' key. RiskAnalysis has 'risks' (list of Risk),
        'residual_risk_summary', 'confidence'.
        """
        risk_analysis = report.get("risk_analysis")
        if not risk_analysis:
            return ""

        # Handle RiskAnalysis as dict (from model_dump)
        if isinstance(risk_analysis, dict):
            risks = risk_analysis.get("risks", [])
            residual = risk_analysis.get("residual_risk_summary", "")
        else:
            return ""

        if not risks:
            return ""

        lines = ["## Risk Analysis", ""]

        # Risk matrix
        lines.append("### Top Risks")
        lines.append("")
        lines.append("| # | Risk | Category | Severity | Probability | Mitigation |")
        lines.append("|---|------|----------|----------|-------------|------------|")

        for i, risk in enumerate(risks[:10], 1):
            if isinstance(risk, dict):
                name = risk.get("name", risk.get("title", ""))
                category = risk.get("category", "")
                severity = risk.get("severity", "")
                probability = risk.get("probability", "")
                mitigation = risk.get("mitigation", risk.get("recommendation", ""))
                lines.append(f"| {i} | {name} | {category} | {severity} | {probability} | {mitigation} |")

        lines.append("")

        if residual:
            lines.append(f"**Residual Risk Summary:** {residual}")
            lines.append("")

        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    def _render_methodology(self, report: dict[str, Any]) -> str:
        """Render the methodology section.

        D18 fix: uses FinalReport top-level fields (agents_used, total_sources,
        total_data_points, limitations) instead of non-existent 'methodology' dict.
        """
        agents_used = report.get("agents_used", [])
        total_sources = report.get("total_sources", 0)
        total_data_points = report.get("total_data_points", 0)
        limitations = report.get("limitations", [])

        if not agents_used and not limitations:
            return ""

        lines = ["## Methodology", ""]

        # Agents used
        if agents_used:
            lines.append("### Agents Deployed")
            lines.append("")
            for agent in agents_used:
                if isinstance(agent, dict):
                    lines.append(f"- **{agent.get('name', '')}**, {agent.get('role', '')}")
                else:
                    lines.append(f"- {agent}")
            lines.append("")

        # Data summary
        if total_sources or total_data_points:
            lines.append("### Data Coverage")
            lines.append("")
            lines.append(f"- **Total unique sources:** {total_sources}")
            lines.append(f"- **Total data points:** {total_data_points}")
            lines.append("")

        # Limitations
        if limitations:
            lines.append("### Limitations")
            lines.append("")
            for limitation in limitations:
                lines.append(f"- {limitation}")
            lines.append("")

        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    def _render_appendix(self, report: dict[str, Any]) -> str:
        """Render the appendix.

        D18 fix: FinalReport has no 'appendix' key. Build appendix from
        sections' sources and contradictions.
        """
        lines = ["## Appendix", ""]

        # Collect all sources from all sections
        all_sources: list[dict[str, Any]] = []
        sections = report.get("sections", [])
        for section in sections:
            if isinstance(section, dict):
                sources = section.get("sources", [])
                for src in sources:
                    if isinstance(src, dict):
                        all_sources.append(src)

        # Deduplicate by URL
        seen_urls: set[str] = set()
        unique_sources: list[dict[str, Any]] = []
        for src in all_sources:
            url = src.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_sources.append(src)

        if unique_sources:
            lines.append("### Sources")
            lines.append("")
            for i, source in enumerate(unique_sources, 1):
                title = source.get("title", "")
                url = source.get("url", "")
                credibility = source.get("credibility", "")
                if url:
                    lines.append(f"{i}. [{title}]({url})" + (f", *{credibility}*" if credibility else ""))
                else:
                    lines.append(f"{i}. {title}")
            lines.append("")

        # Contradictions
        contradictions = report.get("contradictions", [])
        if contradictions:
            lines.append("### Contradictions Resolved")
            lines.append("")
            for contra in contradictions:
                if isinstance(contra, dict):
                    agent_a = contra.get("agent_a", "")
                    agent_b = contra.get("agent_b", "")
                    finding_a = contra.get("finding_a", "")
                    finding_b = contra.get("finding_b", "")
                    resolution = contra.get("resolution", "unresolved")
                    lines.append(f"- **{agent_a}** vs **{agent_b}**: {finding_a} vs {finding_b} → {resolution}")
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
            # D18: Accept both FinalReport Pydantic models and plain dicts
            if hasattr(report_data, "model_dump"):
                report_data = report_data.model_dump()
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
