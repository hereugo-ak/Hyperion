# HYPERION TUI — Internal Design Notes

Short internal doc. The single source of truth is **`HYPERION_INTERFACE_SPEC.md`**
at the repo root; this file just maps the spec onto the code.

## Stack

Python + **Textual** / **Rich** (not Rust — the orchestrator backend it drives
is Python, so the TUI lives in-process and speaks to the real `WorkflowEngine`
and `AgentBus` directly). The spec's Rust stack (§11) is honoured in *design*:
immediate-mode diff rendering, truecolor, zero-idle-repaint, hand-tuned motion.

## Layout

```
hyperion/tui/
├── app.py                 # HyperionApp — single-screen command bridge (§6, §19)
├── theme.py               # §4 palette, badge vocabulary (dependency-light)
├── motion/                # reusable premium motion toolkit (§8)
│   ├── color.py           #   OKLab/OKLCH interpolation — no muddy grey (§3.1)
│   ├── easing.py          #   cubic-bezier(0.16,1,0.3,1) expo-out + standard (§8)
│   └── indicators.py      #   braille spinner, gradient bar, aurora bar (§8.1)
├── widgets/
│   ├── logo.py            # locked wordmark + chroma-sweep intro + shimmer (§2, §3)
│   ├── header.py          # traffic-light dots + version/session; collapsed line (§6)
│   ├── log_stream.py      # badge-tagged rows, fade-in, live indicators, trees (§7,§8,§10)
│   ├── prompt.py          # ◈ hyperion@orchestrator ~ ❯ █ blinking cursor (§9, §8.2)
│   └── rule.py            # thin rules + phase transitions (§2.4, §8.5)
└── screens/session.py     # wires the UI to the REAL engine + bus (§10)
```

## The two things that made it "premium, non-slop"

1. **OKLab gradients.** `motion/color.py` interpolates in perceptual space, so
   cyan→violet→magenta never passes through the grey mid-band that betrays a
   naive sRGB lerp. Verified: `ramp([cyan,violet,magenta], 0.5) == #8B5CF6`.
2. **Idle costs nothing.** Every animation timer stops itself when there is
   nothing left to animate (`LogStream._frame`, `Rule._frame`, logo timer on
   collapse). No repaints while idle (§12).

## Wiring (the fix for "no response")

The previous TUI only *simulated* engagements with `set_timer` and dropped real
questions into an "Unknown command" dead-end. `screens/session.py` now:

- treats any non-slash input as a consulting question,
- launches `WorkflowEngine.run_engagement()` in a background `asyncio` task,
- subscribes to the `AgentBus` (STATUS / FINDINGS / HANDOFF / ESCALATION) and
  streams those events into the log as badge-tagged rows with live spinners,
- surfaces success (DONE + recommendation/PDF) and errors (ERROR) — never
  silently swallows.

Backend imports are **lazy** (inside functions), so the TUI still loads and
renders even if the heavy orchestrator dependencies are absent.

## Reduced motion (§13)

`HyperionApp(reduced_motion=True)` (CLI: `hyperion shell --reduced-motion`):
static logo gradient, no sweep, spinner degrades to a toggling dot, transitions
are instant.
