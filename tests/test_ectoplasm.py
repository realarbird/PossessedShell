from possessed_shell.ectoplasm import EctoplasmMonitor, Reading, describe, snapshot


def test_snapshot_has_vital_signs():
    state = snapshot()
    assert 0 <= state["memory_percent"] <= 100
    assert "CPU" in describe(state)


def test_only_sustained_spikes_raise_omens_and_then_cool_down(settings):
    now = [0.0]
    readings = iter([Reading(99, 10, None)] * 2 + [Reading(10, 10, None)] + [Reading(99, 10, None)] * 6)
    monitor = EctoplasmMonitor(settings, read=lambda: next(readings), clock=lambda: now[0])

    kinds = [[o["kind"] for o in monitor.sample()] for _ in range(6)]
    assert kinds == [[], [], [], [], [], ["cpu"]]  # streak broken once, then 3 in a row
    assert monitor.sample() == []
    now[0] += settings.omen_cooldown + 1
    assert [o["kind"] for o in monitor.sample()] == []  # streak restarted after the omen
    assert monitor.sample()[0]["message"]


def test_hungry_process_is_named(settings):
    monitor = EctoplasmMonitor(settings, read=lambda: Reading(5, 50, {"name": "chrome", "pid": 42, "memory_percent": 45.0}))
    omens = [monitor.sample() for _ in range(3)][-1]
    assert omens[0]["kind"] == "process" and "chrome" in omens[0]["message"] and "42" in omens[0]["fix"]
