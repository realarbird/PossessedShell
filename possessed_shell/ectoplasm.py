"""The Ectoplasm Leak Detector: system snapshots and CPU / RAM spike omens."""

from __future__ import annotations

import os
import random
import shutil
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import psutil

from possessed_shell.config import Settings


def _top_process() -> Optional[Dict]:
    top = None
    for proc in psutil.process_iter(["pid", "name", "memory_percent"]):
        info = proc.info
        if info.get("memory_percent") is None:
            continue
        if top is None or info["memory_percent"] > top["memory_percent"]:
            top = info
    if top is None:
        return None
    return {"pid": top["pid"], "name": top["name"] or "?", "memory_percent": round(top["memory_percent"], 1)}


def snapshot(cwd: str = "") -> Dict:
    """The machine's vital signs at the moment of death, sent to Sentry and to Gemma."""
    vm = psutil.virtual_memory()
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        load = []
    try:
        disk = shutil.disk_usage(cwd if cwd and os.path.isdir(cwd) else os.path.expanduser("~"))
        disk_free = round(disk.free / 2**30, 1)
    except OSError:
        disk_free = None
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": vm.percent,
        "memory_available_gb": round(vm.available / 2**30, 2),
        "load_avg": load,
        "disk_free_gb": disk_free,
        "top_process": _top_process(),
    }


def describe(state: Dict) -> str:
    top = state.get("top_process") or {}
    parts = [f"CPU {state.get('cpu_percent')}%", f"RAM {state.get('memory_percent')}% used"]
    if state.get("disk_free_gb") is not None:
        parts.append(f"{state['disk_free_gb']} GB disk free")
    if top:
        parts.append(f"hungriest process {top.get('name')} (pid {top.get('pid')}, {top.get('memory_percent')}% RAM)")
    return ", ".join(parts)


_CPU_OMENS = [
    "The very air grows hot with fever. Your processors burn at {value:.0f}% like a parlour fire stoked by an "
    "unseen hand, and I fear {name} is the one holding the poker.",
    "Listen... the fans are wailing. {value:.0f}% of your CPU is consumed, as though a legion of the restless "
    "dead were treading the same staircase, over and over.",
]
_MEM_OMENS = [
    "Ectoplasm seeps from the memory banks! {value:.0f}% of your RAM is drowned in it, and {name} drinks "
    "deepest of all.",
    "The house is crowded with spirits; {value:.0f}% of your memory is spoken for. Soon there will be no room "
    "left for the living.",
]
_PROC_OMENS = [
    "Something in this house is feeding. {name} (pid {pid}) has devoured {value:.0f}% of all memory and "
    "still it hungers.",
    "{name} swells like a drowned man in the river, {value:.0f}% of your memory bloating inside it. "
    "Pid {pid}. Remember the name.",
]


@dataclass
class Reading:
    cpu: float
    mem: float
    top: Optional[Dict]


def read_vitals() -> Reading:
    return Reading(cpu=psutil.cpu_percent(interval=None), mem=psutil.virtual_memory().percent, top=_top_process())


class EctoplasmMonitor:
    """Turns repeated vital-sign readings into omens, only once a spike is sustained and off cooldown."""

    SUSTAINED = 3

    def __init__(self, settings: Settings, read: Callable[[], Reading] = read_vitals, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.read = read
        self.clock = clock
        self.streak = {"cpu": 0, "memory": 0, "process": 0}
        self.last_omen: Dict[str, float] = {}

    def _ready(self, kind: str, hot: bool) -> bool:
        self.streak[kind] = self.streak[kind] + 1 if hot else 0
        if self.streak[kind] < self.SUSTAINED:
            return False
        last = self.last_omen.get(kind)
        if last is not None and self.clock() - last < self.settings.omen_cooldown:
            return False
        self.last_omen[kind] = self.clock()
        self.streak[kind] = 0
        return True

    def sample(self) -> List[Dict]:
        r = self.read()
        s = self.settings
        top = r.top or {"name": "an unseen thing", "pid": "?", "memory_percent": 0.0}
        omens = []
        if self._ready("cpu", r.cpu >= s.cpu_threshold):
            omens.append({
                "kind": "cpu", "title": "fevered processors",
                "message": random.choice(_CPU_OMENS).format(value=r.cpu, name=top["name"]),
                "detail": f"CPU at {r.cpu:.0f}% for {self.SUSTAINED} readings in a row.",
                "fix": "ps -eo pid,pcpu,comm --sort=-pcpu | head -5" if os.uname().sysname == "Linux" else "top -o cpu",
                "value": r.cpu,
            })
        if self._ready("memory", r.mem >= s.mem_threshold):
            omens.append({
                "kind": "memory", "title": "drowning memory",
                "message": random.choice(_MEM_OMENS).format(value=r.mem, name=top["name"]),
                "detail": f"RAM at {r.mem:.0f}%; hungriest process is {top['name']} (pid {top['pid']}).",
                "fix": "ps -eo pid,pmem,comm --sort=-pmem | head -5" if os.uname().sysname == "Linux" else "top -o mem",
                "value": r.mem,
            })
        proc_mem = float(top.get("memory_percent") or 0.0)
        if self._ready("process", proc_mem >= s.proc_mem_threshold):
            omens.append({
                "kind": "process", "title": f"{top['name']} is feeding",
                "message": random.choice(_PROC_OMENS).format(value=proc_mem, name=top["name"], pid=top["pid"]),
                "detail": f"{top['name']} (pid {top['pid']}) holds {proc_mem:.0f}% of RAM.",
                "fix": f"ps -o pid,rss,command -p {top['pid']}",
                "value": proc_mem,
            })
        return omens
