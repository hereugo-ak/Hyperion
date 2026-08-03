"""D-07 runtime probe: do Shape-B escalations still collapse to one fingerprint?

Reproduces the audit's exact mechanism without an LLM: build the fingerprint
the Director builds, from the five real Shape-B payloads shipped in the tree.
"""
from __future__ import annotations

# The five real payload shapes, copied verbatim from the current fix0.1 tree.
SHAPE_B_PAYLOADS = [
    # quality_gate.py:1545 — quality gate failed
    {
        "to_agent": "engagement_director",
        "from_agent": "quality_gate",
        "escalation_type": "quality_gate_failed",
        "iteration": 2,
        "total_score": 2.2,
        "threshold": 4.0,
        "critical_dimensions": ["analytical_depth"],
        "escalation_report": "...",
        "message": "Quality Gate FAILED after 2 iterations.",
    },
    # quality_gate.py:1573 — send back for iteration
    {
        "to_agent": "synthesis_lead",
        "from_agent": "quality_gate",
        "task": "iterate",
        "iteration": 3,
        "quality_score": {"total_score": 2.2},
        "message": "Quality Gate: score 2.2/4.0.",
    },
    # fact_checker.py:1187 — unverified claims
    {
        "to_agent": "market_analyst",
        "from_agent": "fact_checker",
        "request_type": "verify_claims",
        "unverified_claims": [{"id": "c1"}],
        "message": "Fact Checker could not verify 4 claim(s).",
    },
    # fact_checker.py:1592 — contradictions
    {
        "agent": "fact_checker",
        "finding_type": "contradictions",
        "contradictions": [{"id": "x1"}],
        "message": "Fact Checker detected 3 contradiction(s).",
    },
    # render_engine.py:1285 — render verification failed
    {
        "to_agent": "presentation_designer",
        "from_agent": "render_engine",
        "task": "fix_layout",
        "issues": ["blank page 4"],
        "pdf_path": "/tmp/r.pdf",
        "message": "Render Engine verification FAILED. 3 issue(s).",
    },
]


def director_read(payload: dict) -> tuple[str, str, str]:
    """Exactly what engagement_director._handle_escalation does (lines 523-539)."""
    issue = payload.get("issue", "Unknown issue")
    agent_name = payload.get("agent", "unknown")
    fingerprint = f"{agent_name}:{issue.strip().lower()[:160]}"
    return agent_name, issue, fingerprint


def main() -> int:
    seen: set[str] = set()
    evaluated = 0
    discarded = 0
    print(f"{'source':<28} {'agent read':<16} {'issue read':<16} verdict")
    print("-" * 82)
    labels = [
        "quality_gate:1545",
        "quality_gate:1573",
        "fact_checker:1187",
        "fact_checker:1592",
        "render_engine:1285",
    ]
    fingerprints = []
    for label, payload in zip(labels, SHAPE_B_PAYLOADS):
        agent, issue, fp = director_read(payload)
        fingerprints.append(fp)
        if fp in seen:
            verdict = "DISCARDED as duplicate"
            discarded += 1
        else:
            seen.add(fp)
            verdict = "evaluated (with EMPTY content)"
            evaluated += 1
        print(f"{label:<28} {agent:<16} {issue:<16} {verdict}")

    print("-" * 82)
    print(f"distinct fingerprints : {len(set(fingerprints))}  -> {sorted(set(fingerprints))}")
    print(f"evaluated             : {evaluated}")
    print(f"discarded as duplicate: {discarded}")
    missing_issue = sum(1 for p in SHAPE_B_PAYLOADS if "issue" not in p)
    print(f"payloads lacking 'issue' key: {missing_issue}/5")
    print()

    # The defect is NOT "all five share one fingerprint" — that was merely how it
    # surfaced in the 07-30 log. The defect is that the Director reads a key the
    # publishers never set, so EVERY Shape-B escalation is content-free at the
    # point of evaluation, and those sharing the collapsed fingerprint are
    # additionally discarded. Either condition alone is silent data loss.
    if missing_issue > 0:
        print("RESULT: D-07 NOT FIXED.")
        print(f"        {missing_issue}/5 publishers omit the 'issue' key the Director reads,")
        print(f"        so {evaluated} escalation(s) reach evaluation with EMPTY content and")
        print(f"        {discarded} are silently discarded as duplicates of it.")
        print("        Adaptive replanning is inoperative for all support agents.")
        return 1
    print("RESULT: every publisher sets 'issue'; the D-07 mechanism is gone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
