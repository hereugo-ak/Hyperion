"""The Jinja environment must humanize every interpolated field (P2-10).

The old ``clean_dict_repr`` filter was applied to exactly 1 of ~40 renderable
fields, and its ``startswith('{')`` guard could not fire on the
``LABEL: {'...'}`` strings that actually leaked. Registering ``humanize`` as
the environment's ``finalize`` hook makes the guarantee structural: no field
can be forgotten.
"""

from __future__ import annotations

import pytest

from hyperion.output.render import TemplateRenderer


def _env():
    return TemplateRenderer()._get_env()


class TestJinjaFinalizer:
    def test_unfiltered_field_is_humanized(self):
        """A plain {{ field }} with NO filter still gets dict reprs cleaned."""
        env = _env()
        template = env.from_string("<div>{{ body }}</div>")
        out = template.render(body="TAM: {'name': 'TAM', 'value': '$12B'}")
        assert "{" not in out
        assert "'name'" not in out
        assert "TAM" in out

    def test_prose_passes_through_unchanged(self):
        env = _env()
        template = env.from_string("<p>{{ text }}</p>")
        out = template.render(text="The market is viable.")
        assert "The market is viable." in out

    def test_unparseable_repr_raises_rather_than_ships(self):
        env = _env()
        template = env.from_string("<div>{{ body }}</div>")
        with pytest.raises(Exception):  # noqa: B017 - any render failure is acceptable
            template.render(body="Build vs Buy: {'recommendation': BUY, !!!")

    def test_clean_dict_repr_filter_still_registered(self):
        env = _env()
        assert "clean_dict_repr" in env.filters

    def test_finalize_hook_is_set(self):
        env = _env()
        assert env.finalize is not None
