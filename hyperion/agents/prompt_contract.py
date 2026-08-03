"""Shared, versioned agent prompt contract (W-16).

PART 2 section 5 measured eight quality clauses whose presence in any given
agent prompt depended entirely on per-file authoring discipline, while the
Part 1 depth audit found that specialists also received no minimum length:

  subject fit / abstain / no fabrication / evidence binding /
  units and denomination / uncertainty / conflict / typography / depth

The measured result was 0/20 out-of-scope clauses, 1/20 anti-fabrication
clauses, 0/20 recency discipline, and a typography rule violated by its own
carrier 16/20 times. P2-32 had already proven the correct pattern (a single
shared string prepended at the dispatch composition point in
``BaseAgent._llm_complete``) but applied it to only one of the eight clauses.

This module is the fix: one string, written once, reviewed once, composed
into EVERY dispatched prompt by ``base.py``. The depth clause is therefore
on the live dispatch path for every specialist call, rather than only on the
Synthesis Lead's section-builder path. Per-agent language that
elaborates the same principles (for example the market analyst's
CAGR-specific abstain instruction) stays in place; the contract is a floor,
not a replacement.

The contract text itself obeys clause 8: it contains no U+2014 / U+2013.
A registry-level test (tests/test_prompt_contract.py) asserts the marker
text reaches the fully composed prompt actually sent to the LLM, so an
agent that bypasses the base composition point fails CI.
"""

from __future__ import annotations

from hyperion.output.typography import PROMPT_TYPOGRAPHY_RULE

# Bump when the contract text changes so prompt-cache keys and audits can
# distinguish which contract version a given run dispatched under.
AGENT_CONTRACT_VERSION = 3

# Stable marker used by the registry-level test to prove the contract
# reached the composed prompt. Derived from the version so the two can
# never drift apart (a bump with a stale marker fails CI immediately).
AGENT_CONTRACT_MARKER = f"HYPERION AGENT CONTRACT v{AGENT_CONTRACT_VERSION}"

AGENT_CONTRACT = (
    f"{AGENT_CONTRACT_MARKER}. These nine clauses bind every agent in this "
    "system, in addition to your role-specific instructions.\n"
    "1. SUBJECT FIT: Before applying any framework, confirm the question's "
    "subject is the kind of entity the framework was built for. Valuation "
    "methods apply to companies and assets, not to nations, regions, or "
    "abstract topics.\n"
    "2. ABSTAIN: If a question, sub-question, or requested artifact falls "
    "outside your analytical competence, say so explicitly as OUT OF SCOPE "
    "and explain why, instead of forcing an inapplicable framework onto it. "
    "A declared abstention is a valid, valued output.\n"
    "3. NO FABRICATION: Never invent figures, sources, citations, quotes, "
    "or events. If the evidence contains no numbers, write without numbers "
    "and say the evidence is qualitative.\n"
    "4. EVIDENCE BINDING: Every material claim must trace to evidence "
    "actually retrieved during this engagement. Cite the source you actually "
    "used; do not cite from memory or training data.\n"
    "5. UNITS AND DENOMINATION: State units, currency, magnitude (millions "
    "vs billions), and per-share vs aggregate basis for every figure. Never "
    "mix denominations without converting and saying so.\n"
    "6. UNCERTAINTY: Attach an explicit confidence level and an as-of date "
    "to every material figure and claim. Treat stale sources as weaker "
    "evidence and say when a figure is dated.\n"
    "7. CONFLICT: When two credible sources disagree, report the "
    "disagreement with both positions and their sources. Never silently "
    "average, pick one, or drop the losing side.\n"
    f"8. TYPOGRAPHY: {PROMPT_TYPOGRAPHY_RULE}\n"
    "9. DEPTH AND LENGTH: For substantive analytical output, write at least "
    "450 words across the analysis or finding content, with evidence, reasoning, "
    "and implications. Compact classification, extraction, and routing responses "
    "are exempt. Never pad weak evidence; state the evidence gap instead."
)


def compose_agent_prompt(role_prompt: str) -> str:
    """Prepend the shared contract to one role-specific system prompt.

    All direct router call sites use this function, including sub-agents and
    support utilities that do not inherit from ``BaseAgent``. The idempotence
    guard prevents nested helpers from dispatching the contract twice.
    """
    prompt = str(role_prompt or "").strip()
    if prompt.startswith(AGENT_CONTRACT_MARKER):
        return prompt
    return f"{AGENT_CONTRACT}\n\n{prompt}"
