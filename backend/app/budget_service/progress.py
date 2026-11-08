import threading

# which budget-graph steps a run has started, in order, so the Alerts page can show the agent working while a run
# is still going. in process memory only: a run started by another process (the cli) shows its finished trace,
# not live steps. only the newest runs are kept

KEEP = 50

_lock = threading.Lock()
_steps: dict[int, list[str]] = {}


def start_step(run_id, name: str) -> None:
    if run_id is None:
        return
    with _lock:
        _steps.setdefault(run_id, []).append(name)
        for old in sorted(_steps)[:-KEEP]:
            del _steps[old]


def steps(run_id: int) -> list[str]:
    with _lock:
        return list(_steps.get(run_id, []))
