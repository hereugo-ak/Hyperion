"""Tests for fix 1.3 — the LLM query planner (audit §4.4 Finding B-3, §7 item 1.3).

The audit's finding was that ``grep -E "_llm_complete|generate.*quer|
query.*llm" hyperion/agents/sub_agent.py`` returned **no matches** — no
agent or sub-agent ever *reasoned* about what to search. Query construction
was a pure regex + stopword pipeline emitting exactly one query per tool.

These tests pin the four properties the audit's item 1.3 actually specifies:

1. **5-10 schema-validated queries** per sub-question.
2. **Diversified** across the entity / metric / counter-thesis / regulatory /
   competitor / time-series angles (the audit names these six verbatim).
3. **FAST tier** — never STRONG/DEEP (the §4.7 quota rule).
4. **Cached by sub-question hash** — the same sub-question must not pay for
   a second planner call.

Plus the non-negotiable safety property that the audit's whole P0 was about:
**a planner failure must degrade to a usable query set, loudly, never to
zero queries and never by raising.**
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from hyperion.config import ModelTier
from hyperion.tools.query_planner import (
    ANGLES,
    MAX_QUERIES,
    MAX_QUERY_LEN,
    MIN_DISTINCT_ANGLES,
    MIN_QUERIES,
    PLANNER_TIER,
    TARGET_QUERIES,
    PlannedQuery,
    QueryPlan,
    clear_plan_cache,
    deterministic_plan,
    plan_cache_stats,
    plan_queries,
    sub_question_hash,
)

# ─────────────────────────────────────────────────────────────────────────
# Fixtures / doubles
# ─────────────────────────────────────────────────────────────────────────

GOOD_PLAN_JSON = """{"queries":[
  {"query":"Nigeria lithium-ion battery manufacturers list","angle":"entity","rationale":"who"},
  {"query":"Nigeria battery market size 2025 CAGR","angle":"metric"},
  {"query":"Nigeria lithium plant cancelled failed project","angle":"counter_thesis"},
  {"query":"NERC battery storage licence Nigeria regulation","angle":"regulatory"},
  {"query":"Nigeria battery importers market share incumbents","angle":"competitor"},
  {"query":"Nigeria battery demand 2019 2024 trend","angle":"time_series"},
  {"query":"Nigeria gigafactory investment announcements","angle":"entity"},
  {"query":"Nigeria energy storage import duty tariff schedule","angle":"regulatory"}
]}"""

QUESTION = "Should we enter the Nigerian lithium-ion battery market now or wait?"


class _Resp:
    def __init__(self, content: str, success: bool = True, error: str = "") -> None:
        self.content = content
        self.success = success
        self.error = error


class _Router:
    """Minimal router double recording every `complete()` call."""

    def __init__(self, content: str = GOOD_PLAN_JSON, success: bool = True) -> None:
        self._resp = _Resp(content, success=success)
        self.calls: list[dict] = []

    async def complete(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        return self._resp


class _BoomRouter:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls += 1
        raise RuntimeError("provider down")


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_plan_cache()
    yield
    clear_plan_cache()


@pytest.fixture(autouse=True)
def _clean_focus():
    from hyperion.tools.query_utils import clear_engagement_focus

    clear_engagement_focus()
    yield
    clear_engagement_focus()


def _run(coro):
    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────────────
# 1. Schema validation
# ─────────────────────────────────────────────────────────────────────────


class TestPlannedQuerySchema:
    """`PlannedQuery` is the "schema-validated" half of item 1.3."""

    def test_valid_query_accepted(self):
        q = PlannedQuery(query="Nigeria battery market size", angle="metric")
        assert q.query == "Nigeria battery market size"
        assert q.angle == "metric"

    @pytest.mark.parametrize("angle", list(ANGLES))
    def test_all_documented_angles_accepted(self, angle):
        assert PlannedQuery(query="valid battery query", angle=angle).angle == angle

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("counter-thesis", "counter_thesis"),
            ("counterthesis", "counter_thesis"),
            ("bear_case", "counter_thesis"),
            ("TIME_SERIES", "time_series"),
            ("timeseries", "time_series"),
            ("trend", "time_series"),
            ("REGULATORY", "regulatory"),
            ("policy", "regulatory"),
            ("competitors", "competitor"),
            ("market size", "metric"),
            ("players", "entity"),
        ],
    )
    def test_angle_aliases_normalized(self, given, expected):
        """A model that writes 'counter-thesis' instead of 'counter_thesis'
        should not lose an otherwise-perfect query to a formatting nit."""
        assert PlannedQuery(query="valid battery query", angle=given).angle == expected

    def test_unknown_angle_rejected(self):
        with pytest.raises(ValueError, match="angle must be one of"):
            PlannedQuery(query="valid battery query", angle="vibes")

    def test_contentless_query_rejected(self):
        """"2024 2025 $100 50%" is the exact contentless pattern
        `query_utils.is_contentless` exists to catch. The planner must never
        be able to *originate* one."""
        with pytest.raises(ValueError):
            PlannedQuery(query="2024 2025 $100 50%", angle="metric")

    def test_empty_query_rejected(self):
        with pytest.raises(ValueError):
            PlannedQuery(query="   ", angle="metric")

    def test_overlong_query_rejected_by_max_length(self):
        with pytest.raises(ValueError):
            PlannedQuery(query="battery " * 40, angle="metric")


# ─────────────────────────────────────────────────────────────────────────
# 2. Cache keying
# ─────────────────────────────────────────────────────────────────────────


class TestSubQuestionHash:
    """"cache by sub-question hash" (audit §7 item 1.3)."""

    def test_identical_questions_hash_identically(self):
        assert sub_question_hash(QUESTION) == sub_question_hash(QUESTION)

    def test_case_and_whitespace_and_punctuation_normalized(self):
        a = sub_question_hash("Market size in Nigeria?")
        b = sub_question_hash("  market   size in NIGERIA  ")
        assert a == b

    def test_different_questions_hash_differently(self):
        assert sub_question_hash("market size Nigeria") != sub_question_hash("market size Kenya")

    def test_subject_participates_in_key(self):
        """The same literal sub-question under a different engagement subject
        must not reuse the other engagement's plan."""
        a = sub_question_hash("what is the regulatory outlook", subject="lithium batteries")
        b = sub_question_hash("what is the regulatory outlook", subject="offshore wind")
        assert a != b

    def test_geography_participates_in_key(self):
        a = sub_question_hash("market size", geography="Nigeria")
        b = sub_question_hash("market size", geography="Kenya")
        assert a != b

    def test_hash_is_stable_string(self):
        h = sub_question_hash(QUESTION)
        assert isinstance(h, str) and len(h) == 32


# ─────────────────────────────────────────────────────────────────────────
# 3. Deterministic plan — the guaranteed floor
# ─────────────────────────────────────────────────────────────────────────


class TestDeterministicPlan:
    """The deterministic plan is what makes a planner outage survivable.

    It must be diversified, count-compliant, and never raise — because it is
    the code path that runs when the LLM is down, and the audit's P0 was
    precisely a silent failure in the query layer.
    """

    def test_meets_min_query_count(self):
        plan = deterministic_plan(QUESTION, subject="lithium-ion batteries", geography="Nigeria")
        assert MIN_QUERIES <= len(plan.queries) <= MAX_QUERIES

    def test_reaches_target_query_count(self):
        plan = deterministic_plan(QUESTION, subject="lithium-ion batteries", geography="Nigeria")
        assert len(plan.queries) >= TARGET_QUERIES

    def test_covers_all_six_audit_angles(self):
        plan = deterministic_plan(QUESTION, subject="lithium-ion batteries", geography="Nigeria")
        assert plan.angles_covered == set(ANGLES)

    def test_marked_degraded(self):
        """A deterministic plan is a *degradation*. It must say so, so a
        planner outage is visible in logs/metrics rather than looking like
        a healthy plan."""
        assert deterministic_plan(QUESTION).degraded is True

    def test_first_query_is_the_pre_planner_baseline(self):
        """Fix 1.3 must be strictly additive: the plain condensed query
        (pre-1.3 behaviour) has to survive as the first candidate.

        Token-equivalence, not string-equality: the deterministic plan
        additionally strips interrogative punctuation, because a '?' sitting
        mid-string once an angle suffix is appended ("... market wait?
        regulation compliance") is a broken keyword query. The *tokens* must
        be identical, which is what "same query" means to a search engine.
        `sub_agent._plan_queries` separately dispatches the unmodified
        `_condense_query_variants` output too, so nothing is lost either way.
        """
        from hyperion.agents.sub_agent import SubAgentRunner

        plan = deterministic_plan(QUESTION)
        expected = SubAgentRunner._condense_query(QUESTION, max_len=MAX_QUERY_LEN)
        assert plan.queries[0].query.split() == expected.rstrip("?").split()

    def test_all_queries_within_length_cap(self):
        plan = deterministic_plan(
            "Should the Federal Republic of Nigeria pursue domestic lithium-ion "
            "battery cell manufacturing capacity now, or wait for the regional "
            "market to mature over the next planning horizon?",
            subject="lithium-ion battery cell manufacturing",
            geography="Nigeria",
        )
        assert all(len(q.query) <= MAX_QUERY_LEN for q in plan.queries)

    def test_angle_keywords_survive_truncation(self):
        """A 'regulatory' query whose only regulatory keyword got truncated
        away is not a regulatory query. The anchor is pre-trimmed to reserve
        room for the angle suffix — this asserts that actually works."""
        plan = deterministic_plan(
            "Should the Federal Republic of Nigeria pursue domestic lithium-ion "
            "battery cell manufacturing capacity now, or wait for the regional "
            "market to mature over the next planning horizon?",
            subject="lithium-ion battery cell manufacturing capacity expansion",
            geography="Nigeria",
        )
        for q in plan.queries:
            if q.angle == "regulatory":
                assert "regulat" in q.query.lower() or "policy" in q.query.lower()
            if q.angle == "counter_thesis":
                assert any(w in q.query.lower() for w in ("risk", "fail", "criticism", "problem"))

    def test_no_interrogative_punctuation_mid_query(self):
        """'... market wait? regulation compliance' is a broken keyword
        query. The '?' must be stripped before the suffix is appended."""
        plan = deterministic_plan(QUESTION, subject="lithium-ion batteries", geography="Nigeria")
        for q in plan.queries:
            assert "?" not in q.query

    def test_geography_anchored_in_most_queries(self):
        plan = deterministic_plan(QUESTION, subject="batteries", geography="Nigeria")
        anchored = sum(1 for q in plan.queries if "niger" in q.query.lower())
        assert anchored >= len(plan.queries) // 2

    def test_queries_are_distinct(self):
        plan = deterministic_plan(QUESTION, subject="batteries", geography="Nigeria")
        assert len({q.query for q in plan.queries}) == len(plan.queries)

    @pytest.mark.parametrize(
        "question",
        [
            "",
            "   ",
            "?",
            "a",
            "Find market size — 2024 data",
            "Should we enter now or wait? (Bitcoin, Ethereum)",
            "Análisis del mercado de baterías en Nigeria",
            "x" * 500,
            "\u2014\u2013--",
        ],
    )
    def test_never_raises_on_adversarial_input(self, question):
        """Same adversarial corpus as the Phase 0 `_condense_query` tests —
        the planner sits directly downstream of it."""
        plan = deterministic_plan(question)
        assert isinstance(plan, QueryPlan)


# ─────────────────────────────────────────────────────────────────────────
# 4. plan_queries — the LLM path
# ─────────────────────────────────────────────────────────────────────────


class TestPlanQueriesLLMPath:
    def test_returns_five_to_ten_queries(self):
        plan = _run(plan_queries(QUESTION, router=_Router()))
        assert MIN_QUERIES <= len(plan.queries) <= MAX_QUERIES

    def test_meets_audit_target_count(self):
        plan = _run(plan_queries(QUESTION, router=_Router()))
        assert len(plan.queries) >= TARGET_QUERIES

    def test_covers_at_least_min_distinct_angles(self):
        plan = _run(plan_queries(QUESTION, router=_Router()))
        assert len(plan.angles_covered) >= MIN_DISTINCT_ANGLES

    def test_covers_all_six_angles_from_a_good_plan(self):
        plan = _run(plan_queries(QUESTION, router=_Router()))
        assert plan.angles_covered == set(ANGLES)

    def test_not_degraded_when_llm_succeeds(self):
        plan = _run(plan_queries(QUESTION, router=_Router()))
        assert plan.degraded is False

    def test_runs_at_fast_tier(self):
        """Audit §7 item 1.3: "Run at FAST tier". Sub-agent-adjacent work must
        never burn STRONG/DEEP quota (§4.7)."""
        router = _Router()
        _run(plan_queries(QUESTION, router=router))
        assert router.calls[0]["tier"] is ModelTier.FAST
        assert PLANNER_TIER is ModelTier.FAST

    def test_never_escalates_tier(self):
        router = _Router()
        _run(plan_queries(QUESTION, router=router))
        assert router.calls[0]["tier"] not in (
            ModelTier.STANDARD,
            ModelTier.STRONG,
            ModelTier.DEEP,
        )

    def test_uses_low_urgency(self):
        from hyperion.router.budget import TaskUrgency

        router = _Router()
        _run(plan_queries(QUESTION, router=router))
        assert router.calls[0]["urgency"] is TaskUrgency.LOW

    def test_requests_json_object_response_format(self):
        router = _Router()
        _run(plan_queries(QUESTION, router=router))
        assert router.calls[0]["response_format"] == {"type": "json_object"}

    def test_prompt_names_all_six_angles(self):
        router = _Router()
        _run(plan_queries(QUESTION, router=router))
        system = router.calls[0]["messages"][0]["content"]
        for angle in ANGLES:
            assert angle in system

    def test_subject_and_geography_reach_the_prompt(self):
        router = _Router()
        _run(
            plan_queries(
                QUESTION, router=router, subject="lithium-ion batteries", geography="Nigeria"
            )
        )
        user = router.calls[0]["messages"][1]["content"]
        assert "lithium-ion batteries" in user
        assert "Nigeria" in user

    def test_context_reaches_the_prompt(self):
        router = _Router()
        _run(plan_queries(QUESTION, router=router, context={"segment": "grid storage"}))
        user = router.calls[0]["messages"][1]["content"]
        assert "grid storage" in user


class TestPlanQueriesValidationHardening:
    """The LLM's output is never trusted."""

    def test_invalid_angle_query_dropped_but_plan_survives(self):
        router = _Router(
            '{"queries":[{"query":"good battery query here","angle":"entity"},'
            '{"query":"bad angle query here","angle":"vibes"}]}'
        )
        plan = _run(plan_queries(QUESTION, router=router))
        assert "bad angle query here" not in plan.query_strings
        assert "good battery query here" in plan.query_strings

    def test_contentless_query_dropped(self):
        router = _Router(
            '{"queries":[{"query":"good battery query here","angle":"entity"},'
            '{"query":"2024 2025 $100 50%","angle":"metric"}]}'
        )
        plan = _run(plan_queries(QUESTION, router=router))
        assert "2024 2025 $100 50%" not in plan.query_strings

    def test_duplicate_queries_collapsed(self):
        router = _Router(
            '{"queries":['
            '{"query":"Nigeria battery manufacturers list","angle":"entity"},'
            '{"query":"Nigeria battery manufacturers list","angle":"competitor"}]}'
        )
        plan = _run(plan_queries(QUESTION, router=router))
        assert plan.query_strings.count("Nigeria battery manufacturers list") == 1

    def test_near_duplicate_word_order_collapsed(self):
        router = _Router(
            '{"queries":['
            '{"query":"Nigeria battery manufacturers","angle":"entity"},'
            '{"query":"manufacturers battery Nigeria","angle":"competitor"}]}'
        )
        plan = _run(plan_queries(QUESTION, router=router))
        planner_originated = [
            q for q in plan.query_strings if set(q.lower().split()) == {
                "nigeria", "battery", "manufacturers"
            }
        ]
        assert len(planner_originated) == 1

    def test_overlong_query_truncated_not_dropped(self):
        long_q = "Nigeria lithium ion battery " * 10
        router = _Router(f'{{"queries":[{{"query":"{long_q}","angle":"metric"}}]}}')
        plan = _run(plan_queries(QUESTION, router=router))
        assert all(len(q.query) <= MAX_QUERY_LEN for q in plan.queries)
        assert any("Nigeria lithium ion battery" in q.query for q in plan.queries)

    def test_bare_string_list_accepted(self):
        """Some models ignore the object schema and return plain strings.
        Accept them rather than discarding a usable plan."""
        router = _Router('{"queries":["Nigeria battery market size","Nigeria battery risks"]}')
        plan = _run(plan_queries(QUESTION, router=router))
        assert "Nigeria battery market size" in plan.query_strings

    def test_top_level_list_accepted(self):
        router = _Router('[{"query":"Nigeria battery market size","angle":"metric"}]')
        plan = _run(plan_queries(QUESTION, router=router))
        assert "Nigeria battery market size" in plan.query_strings

    def test_json_in_code_fence_accepted(self):
        router = _Router(
            "Here you go:\n```json\n"
            '{"queries":[{"query":"Nigeria battery market size","angle":"metric"}]}'
            "\n```"
        )
        plan = _run(plan_queries(QUESTION, router=router))
        assert "Nigeria battery market size" in plan.query_strings

    def test_internal_agent_vocabulary_stripped(self):
        """Audit §4.9 Finding B-8: internal agent names must never appear in
        an outbound query. The planner is a new outbound-query source, so it
        gets the same guarantee."""
        router = _Router(
            '{"queries":[{"query":"market analyst Nigeria battery outlook","angle":"entity"}]}'
        )
        plan = _run(plan_queries(QUESTION, router=router, parent_agent="market_analyst"))
        joined = " ".join(plan.query_strings).lower()
        assert "market analyst" not in joined
        assert "analyst" not in joined

    def test_subject_word_market_is_not_stripped_as_agent_vocabulary(self):
        """Regression guard for a bug found during live verification of this
        fix: splitting "market_analyst" into tokens and stripping each one
        deleted the word "market" from "Nigeria battery market size 2025" —
        destroying the very query the sanitizer was meant to protect. Only
        the full agent *phrase* may be stripped."""
        router = _Router(
            '{"queries":[{"query":"Nigeria battery market size 2025 CAGR","angle":"metric"}]}'
        )
        plan = _run(plan_queries(QUESTION, router=router, parent_agent="market_analyst"))
        assert any("market size" in q.lower() for q in plan.query_strings)

    def test_trailing_question_mark_stripped(self):
        router = _Router(
            '{"queries":[{"query":"what is the Nigeria battery market size?","angle":"metric"}]}'
        )
        plan = _run(plan_queries(QUESTION, router=router))
        assert not any(q.endswith("?") for q in plan.query_strings)


class TestPlanQueriesDegradation:
    """The audit's central lesson: a query-layer failure must be loud and
    survivable, never silent and total."""

    def test_router_exception_degrades_not_raises(self):
        plan = _run(plan_queries(QUESTION, router=_BoomRouter()))
        assert len(plan.queries) >= MIN_QUERIES
        assert plan.degraded is True

    def test_unsuccessful_response_degrades(self):
        plan = _run(plan_queries(QUESTION, router=_Router("", success=False)))
        assert len(plan.queries) >= MIN_QUERIES
        assert plan.degraded is True

    def test_non_json_response_degrades(self):
        plan = _run(plan_queries(QUESTION, router=_Router("I cannot help with that.")))
        assert len(plan.queries) >= MIN_QUERIES
        assert plan.degraded is True

    def test_empty_queries_list_degrades(self):
        plan = _run(plan_queries(QUESTION, router=_Router('{"queries":[]}')))
        assert len(plan.queries) >= MIN_QUERIES
        assert plan.degraded is True

    def test_all_queries_invalid_degrades(self):
        plan = _run(
            plan_queries(
                QUESTION,
                router=_Router('{"queries":[{"query":"12 34","angle":"nope"}]}'),
            )
        )
        assert len(plan.queries) >= MIN_QUERIES
        assert plan.degraded is True

    def test_degradation_is_logged_at_warning(self, caplog):
        """A degraded planner must be visible in logs — the audit's P0 hid
        behind `except Exception: pass`."""
        with caplog.at_level(logging.WARNING, logger="hyperion.tools.query_planner"):
            _run(plan_queries(QUESTION, router=_BoomRouter()))
        assert any(
            "query planner" in r.message.lower() or "query planner" in r.getMessage().lower()
            for r in caplog.records
        )

    def test_no_router_still_returns_a_plan(self):
        plan = _run(plan_queries(QUESTION, router=None, use_cache=False))
        assert len(plan.queries) >= 1

    def test_empty_question_returns_empty_plan_without_raising(self):
        plan = _run(plan_queries("   ", router=_Router()))
        assert plan.queries == []
        assert plan.degraded is True

    def test_llm_underdelivery_is_topped_up_to_target(self):
        """A model returning only 2 queries must not silently halve research
        coverage — the deterministic angles top it back up."""
        router = _Router(
            '{"queries":[{"query":"Nigeria battery manufacturers list","angle":"entity"},'
            '{"query":"Nigeria battery market size 2025","angle":"metric"}]}'
        )
        plan = _run(plan_queries(QUESTION, router=router, subject="batteries", geography="Nigeria"))
        assert len(plan.queries) >= TARGET_QUERIES
        assert len(plan.angles_covered) >= MIN_DISTINCT_ANGLES
        # The LLM's own queries must be preserved, and ranked first.
        assert plan.query_strings[0] == "Nigeria battery manufacturers list"

    def test_llm_overdelivery_is_clamped_to_max(self):
        items = ",".join(
            f'{{"query":"Nigeria battery query number {i} here","angle":"metric"}}'
            for i in range(25)
        )
        plan = _run(plan_queries(QUESTION, router=_Router(f'{{"queries":[{items}]}}')))
        assert len(plan.queries) <= MAX_QUERIES


class TestPlanQueriesCache:
    """"cache by sub-question hash" (audit §7 item 1.3)."""

    def test_second_identical_call_does_not_hit_the_llm(self):
        router = _Router()
        _run(plan_queries(QUESTION, router=router))
        _run(plan_queries(QUESTION, router=router))
        assert len(router.calls) == 1

    def test_cached_plan_is_flagged_cached(self):
        router = _Router()
        first = _run(plan_queries(QUESTION, router=router))
        second = _run(plan_queries(QUESTION, router=router))
        assert first.cached is False
        assert second.cached is True

    def test_cached_plan_has_the_same_queries(self):
        router = _Router()
        first = _run(plan_queries(QUESTION, router=router))
        second = _run(plan_queries(QUESTION, router=router))
        assert first.query_strings == second.query_strings

    def test_normalized_variants_share_one_cache_entry(self):
        """Two specialists phrasing the same sub-question with different
        case/whitespace/punctuation must cost one planner call, not two."""
        router = _Router()
        _run(plan_queries("Market size in Nigeria?", router=router))
        _run(plan_queries("  market   SIZE in nigeria  ", router=router))
        assert len(router.calls) == 1

    def test_different_subject_is_a_cache_miss(self):
        router = _Router()
        _run(plan_queries("regulatory outlook", router=router, subject="lithium batteries"))
        _run(plan_queries("regulatory outlook", router=router, subject="offshore wind"))
        assert len(router.calls) == 2

    def test_use_cache_false_bypasses_cache(self):
        router = _Router()
        _run(plan_queries(QUESTION, router=router))
        _run(plan_queries(QUESTION, router=router, use_cache=False))
        assert len(router.calls) == 2

    def test_clear_plan_cache_forces_a_fresh_call(self):
        router = _Router()
        _run(plan_queries(QUESTION, router=router))
        clear_plan_cache()
        _run(plan_queries(QUESTION, router=router))
        assert len(router.calls) == 2

    def test_cache_stats_reported(self):
        router = _Router()
        _run(plan_queries(QUESTION, router=router))
        _run(plan_queries(QUESTION, router=router))
        stats = plan_cache_stats()
        assert stats["entries"] == 1
        assert stats["hits"] >= 1
        assert stats["misses"] >= 1

    def test_degraded_plan_is_still_cached(self):
        """A planner outage should not cause a retry storm — one failed call
        per sub-question, then reuse the deterministic plan."""
        router = _BoomRouter()
        _run(plan_queries(QUESTION, router=router))
        _run(plan_queries(QUESTION, router=router))
        assert router.calls == 1


# ─────────────────────────────────────────────────────────────────────────
# 5. sub_agent.py wiring
# ─────────────────────────────────────────────────────────────────────────


def _make_runner(question: str, tools: list, router):
    from unittest.mock import MagicMock

    from hyperion.schemas.agents import AgentName, SubAgentSpec

    spec = SubAgentSpec(
        question=question,
        parent_agent=AgentName.MARKET_ANALYST,
        model_tier=ModelTier.MICRO,
        tools=tools,
        findings_model="KeyFinding",
    )
    from hyperion.agents.sub_agent import SubAgentRunner

    return SubAgentRunner(spec, bus=MagicMock(), router=router)


class _FakeResult:
    def __init__(self, url: str) -> None:
        self.url = url
        self.title = "t"
        self.snippet = "s"


def _search_double():
    """Search double returning >= LOW_YIELD_THRESHOLD distinct results per
    query, so the fix-1.5 low-yield retry never engages and cannot perturb
    these tests' call counts."""
    from unittest.mock import AsyncMock

    def _side_effect(q, **kwargs):
        base = abs(hash(q)) % 100000
        return [
            _FakeResult(f"https://example.com/{base}"),
            _FakeResult(f"https://example.com/{base}-b"),
            _FakeResult(f"https://example.com/{base}-c"),
        ]

    return AsyncMock(side_effect=_side_effect)


class TestSubAgentUsesThePlanner:
    """The audit's grep — no LLM query reasoning anywhere in
    `sub_agent.py` — must now come back positive, and the queries must
    actually reach the network boundary."""

    def test_sub_agent_module_references_the_planner(self):
        import hyperion.agents.sub_agent as mod

        src = open(mod.__file__).read()
        assert "query_planner" in src
        assert "plan_queries" in src

    def test_plan_queries_returns_multiple_queries(self):
        runner = _make_runner(QUESTION, [], _Router())
        queries = _run(runner._plan_queries())
        assert len(queries) >= MIN_QUERIES

    def test_plan_queries_includes_the_condense_baseline(self):
        """Fix 1.3 is additive: the fix-1.4 deterministic variant must still
        be dispatched regardless of what the planner returned."""
        from hyperion.agents.sub_agent import SubAgentRunner

        runner = _make_runner(QUESTION, [], _Router())
        queries = _run(runner._plan_queries())
        for baseline in SubAgentRunner._condense_query_variants(QUESTION):
            assert baseline in queries

    def test_searxng_leg_dispatches_planner_queries(self):
        from hyperion.schemas.agents import ToolName

        spy = _search_double()
        runner = _make_runner(QUESTION, [ToolName.SEARXNG], _Router())
        runner._tools["searxng"] = type("F", (), {"search": spy})()

        _run(runner._search_searxng())

        dispatched = [c.args[0] for c in spy.await_args_list]
        assert len(dispatched) >= 2, dispatched
        assert any("manufacturers" in q or "cancelled" in q for q in dispatched), dispatched

    def test_jina_leg_dispatches_planner_queries(self):
        from hyperion.schemas.agents import ToolName

        spy = _search_double()
        runner = _make_runner(QUESTION, [ToolName.JINA], _Router())
        runner._tools["jina"] = type("F", (), {"search": spy})()

        _run(runner._search_jina())

        dispatched = [c.args[0] for c in spy.await_args_list]
        assert len(dispatched) >= 2, dispatched

    def test_legs_are_partitioned_not_duplicated(self):
        """Each leg gets a *different* slice of the plan, so the two engines
        cover disjoint angles instead of paying twice for the same query."""
        from hyperion.schemas.agents import ToolName

        sx, jn = _search_double(), _search_double()
        runner = _make_runner(QUESTION, [ToolName.SEARXNG, ToolName.JINA], _Router())
        runner._tools["searxng"] = type("F", (), {"search": sx})()
        runner._tools["jina"] = type("F", (), {"search": jn})()

        _run(runner._search_searxng())
        _run(runner._search_jina())

        qsx = {c.args[0] for c in sx.await_args_list}
        qjn = {c.args[0] for c in jn.await_args_list}
        # Overlap is limited to the shared deterministic baseline.
        from hyperion.agents.sub_agent import SubAgentRunner

        baseline = set(SubAgentRunner._condense_query_variants(QUESTION))
        assert (qsx & qjn) <= baseline

    def test_union_across_legs_meets_audit_exit_criterion(self):
        """Audit Phase 1 exit criterion: ">=8 distinct grounded queries per
        sub-question"."""
        from hyperion.schemas.agents import ToolName

        sx, jn = _search_double(), _search_double()
        runner = _make_runner(QUESTION, [ToolName.SEARXNG, ToolName.JINA], _Router())
        runner._tools["searxng"] = type("F", (), {"search": sx})()
        runner._tools["jina"] = type("F", (), {"search": jn})()

        _run(runner._search_searxng())
        _run(runner._search_jina())

        union = {c.args[0] for c in sx.await_args_list} | {
            c.args[0] for c in jn.await_args_list
        }
        assert len(union) >= 8, sorted(union)

    def test_both_legs_share_one_planner_call_via_cache(self):
        from hyperion.schemas.agents import ToolName

        router = _Router()
        sx, jn = _search_double(), _search_double()
        runner = _make_runner(QUESTION, [ToolName.SEARXNG, ToolName.JINA], router)
        runner._tools["searxng"] = type("F", (), {"search": sx})()
        runner._tools["jina"] = type("F", (), {"search": jn})()

        _run(runner._search_searxng())
        _run(runner._search_jina())

        assert len(router.calls) == 1

    def test_planner_failure_falls_back_to_condense_variants(self):
        from hyperion.agents.sub_agent import SubAgentRunner

        runner = _make_runner(QUESTION, [], _BoomRouter())
        queries = _run(runner._plan_queries())
        for baseline in SubAgentRunner._condense_query_variants(QUESTION):
            assert baseline in queries
        assert len(queries) >= 1

    def test_search_leg_survives_total_planner_failure(self):
        """A planner outage must not reproduce the audit's P0 — the search
        leg still runs and still returns results."""
        from hyperion.schemas.agents import ToolName

        spy = _search_double()
        runner = _make_runner(QUESTION, [ToolName.SEARXNG], _BoomRouter())
        runner._tools["searxng"] = type("F", (), {"search": spy})()

        label, urls, formatted = _run(runner._search_searxng())
        assert label == "searxng"
        assert urls, "search leg produced no URLs under planner failure"
        assert spy.await_count >= 1

    def test_planner_tier_is_fast_even_though_sub_agent_is_micro(self):
        """The sub-agent itself runs MICRO; the planner is pinned FAST by the
        module constant, not inherited from the caller's tier."""
        from hyperion.schemas.agents import ToolName

        router = _Router()
        spy = _search_double()
        runner = _make_runner(QUESTION, [ToolName.SEARXNG], router)
        runner._tools["searxng"] = type("F", (), {"search": spy})()
        assert runner.tier is ModelTier.MICRO

        _run(runner._search_searxng())

        assert router.calls[0]["tier"] is ModelTier.FAST

    @pytest.mark.parametrize(
        "question",
        [
            "Find market size — 2024 data",
            "Should we enter now or wait? (Bitcoin, Ethereum)",
            "x" * 400,
            "\u2014\u2013--",
            "?",
        ],
    )
    def test_plan_queries_never_raises_and_never_empty(self, question):
        runner = _make_runner(question, [], _Router())
        queries = _run(runner._plan_queries())
        assert isinstance(queries, list)
        assert len(queries) >= 1
