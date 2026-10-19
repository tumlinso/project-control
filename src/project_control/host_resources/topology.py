"""Runtime GPU topology discovery without fixed device or cable assumptions."""

from __future__ import annotations

import csv
import io
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass


Runner = Callable[[list[str]], str]
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _run(argv: list[str]) -> str:
    return subprocess.run(
        argv, text=True, capture_output=True, check=True, timeout=10,
    ).stdout


@dataclass(frozen=True)
class LocalWorkerHostTopology:
    """Live topology classification used by local-worker admission."""

    mode: str
    gpu_count: int | None
    nvlink_components: tuple[tuple[int, ...], ...]
    status: str


def _parse_topology_matrix(topology: str) -> tuple[
        tuple[int, ...], dict[int, dict[int, str]], dict[int, str], bool]:
    links: dict[int, dict[int, str]] = {}
    numa: dict[int, str] = {}
    header: list[str] = []
    gpu_columns: list[tuple[int, int]] = []
    numa_column: int | None = None
    complete = True
    for raw in topology.splitlines():
        clean = ANSI_ESCAPE.sub("", raw)
        if "\t" in clean:
            fields = [item.strip() for item in clean.split("\t")]
            if fields and not fields[0]:
                fields.pop(0)
        else:
            fields = clean.split()
        if not fields:
            continue
        if len(fields) > 1 and fields[0].startswith("GPU") and fields[1].startswith("GPU"):
            header = fields
            gpu_columns = [
                (header.index(name) + 1, int(name[3:]))
                for name in header if name.startswith("GPU") and name[3:].isdigit()
            ]
            for name in ("NUMA Affinity", "NUMA_Affinity"):
                if name in header:
                    numa_column = header.index(name)
                    break
            continue
        if fields[0].startswith("GPU") and fields[0][3:].isdigit():
            index = int(fields[0][3:])
            links[index] = {}
            for column, peer in gpu_columns:
                if column >= len(fields):
                    complete = False
                    continue
                links[index][peer] = fields[column]
            if numa_column is not None and numa_column + 1 < len(fields):
                value = fields[numa_column + 1]
                if value.lstrip("-").isdigit():
                    numa[index] = value
    indices = tuple(sorted(peer for _, peer in gpu_columns))
    if not indices or set(links) != set(indices):
        complete = False
    if any(set(adjacent) != set(indices) for adjacent in links.values()):
        complete = False
    return indices, links, numa, complete


def _nvlink_components(indices: tuple[int, ...], links: dict[int, dict[int, str]]) -> tuple[tuple[int, ...], ...]:
    parent = {index: index for index in indices}

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for left, adjacent in links.items():
        for right, relation in adjacent.items():
            if relation.startswith("NV") and right in parent:
                union(left, right)
    groups: dict[int, list[int]] = {}
    for index in indices:
        groups.setdefault(find(index), []).append(index)
    return tuple(sorted((tuple(sorted(group)) for group in groups.values())))


def classify_local_worker_host_topology(runner: Runner = _run) -> LocalWorkerHostTopology:
    """Classify a fresh ``nvidia-smi topo -m`` observation for local work."""
    try:
        topology = runner(["nvidia-smi", "topo", "-m"])
        indices, links, _, complete = _parse_topology_matrix(topology)
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return LocalWorkerHostTopology("unknown", None, (), "unavailable")
    if not complete:
        return LocalWorkerHostTopology("unknown", len(indices) or None, (), "unavailable")
    components = _nvlink_components(indices, links)
    sizes = sorted(len(component) for component in components)
    if len(indices) == 4 and sizes == [2, 2]:
        return LocalWorkerHostTopology("normal", 4, components, "available")
    if len(indices) == 4 and sizes == [4]:
        return LocalWorkerHostTopology("x_mode", 4, components, "available")
    return LocalWorkerHostTopology("unknown", len(indices), components, "unsupported")


def discover_gpu_topology(runner: Runner = _run) -> list[dict[str, object]]:
    fields = ["index", "uuid", "pci.bus_id", "pcie.link.gen.current", "pcie.link.width.current",
              "memory.free", "utilization.gpu"]
    try:
        inventory = runner(["nvidia-smi", "--query-gpu=" + ",".join(fields), "--format=csv,noheader,nounits"])
    except (OSError, subprocess.SubprocessError):
        fields = fields[:3]
        inventory = runner(["nvidia-smi", "--query-gpu=" + ",".join(fields), "--format=csv,noheader,nounits"])
    topology = runner(["nvidia-smi", "topo", "-m"])
    devices: dict[int, dict[str, str]] = {}
    for row in csv.reader(io.StringIO(inventory)):
        if len(row) >= 3:
            index = int(row[0].strip())
            values = [item.strip() for item in row]
            devices[index] = {
                "uuid": values[1], "pci_bus_id": values[2],
                "pcie_generation": values[3] if len(values) > 3 and values[3] else "unknown",
                "pcie_link_width": values[4] if len(values) > 4 and values[4] else "unknown",
                "memory_free_mib": values[5] if len(values) > 5 and values[5] else "unknown",
                "utilization_percent": values[6] if len(values) > 6 and values[6] else "unknown",
            }

    _, links, numa, _ = _parse_topology_matrix(topology)
    components = _nvlink_components(tuple(sorted(devices)), links)
    group_for = {index: component for component in components for index in component}

    resources: list[dict[str, object]] = []
    for index, device in sorted(devices.items()):
        domain = device["pci_bus_id"].split(":", 1)[0]
        numa_node = numa.get(index, "unknown")
        island = "-".join(str(item) for item in group_for.get(index, (index,)))
        resources.append({
            "id": f"accelerator:{device['uuid']}",
            "kind": "accelerator",
            "tags": {
                "index": str(index),
                "uuid": device["uuid"],
                "pci_bus_id": device["pci_bus_id"],
                "numa_node": numa_node,
                "pcie_root": f"{domain}:numa:{numa_node}",
                "nvlink_domain": f"runtime:{island}",
                "pcie_generation_current": device["pcie_generation"],
                "pcie_link_width_current": device["pcie_link_width"],
                "memory_free_mib": device["memory_free_mib"],
                "utilization_percent": device["utilization_percent"],
            },
            "enabled": True,
        })
    return resources
