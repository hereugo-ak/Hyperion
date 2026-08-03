import asyncio
import traceback


async def test():
    from hyperion.agents.bus import get_bus, reset_bus
    from hyperion.orchestrator import WorkflowEngine

    reset_bus()
    bus = get_bus()
    await bus.start()
    engine = WorkflowEngine(bus=bus)
    try:
        result = await engine.run_engagement(question='should India enter the EV market?')
        print(f"SUCCESS: {result.success}")
        print(f"ERROR: {result.error}")
        if result.dag:
            for t in result.dag.tasks:
                print(f"  {t.id}: {t.status.value} — {t.error or 'ok'}")
        if result.final_report:
            print(f"  REPORT: {result.final_report.recommendation.value}")
    except Exception:  # noqa: BLE001 - diagnostic script must print any failure
        # D5.1: `as e` was unused (ruff F841); print_exc() already reports it.
        traceback.print_exc()
    finally:
        await engine.close()

asyncio.run(test())
