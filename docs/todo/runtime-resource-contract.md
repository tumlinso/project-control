# CORE4 host resource policy v1

The host database coordinates physical ownership only. Todo tasks, claims,
evidence, and retry state remain project-local todo-orchestrator authority.

The fixed priority order, highest first, is `clean_cuda_foreground`,
`active_local_delegation`, `foreground_gpu`, `background_cuda`, and
`idle_model_residency`. A requester may signal preemption only to conflicting,
preemptible owners below it. Equal or higher owners are never displaced.

Selection examines runtime resource IDs and topology tags. It first chooses a
free compatible bundle; therefore an available spare island does not disturb a
resident service. No GPU index or cabling layout is part of this contract.

Service owners use `reserve_service`, `set_priority`, `heartbeat`,
`preempt_requested`, and `release`. Preemption is cooperative: the owner drains,
evicts, and releases physical resources. Stale dead processes are swept without
altering project task state. Existing background and foreground APIs remain
supported; CUDA clean-foreground callers opt into the highest named class.

The supported runtime facade exposes service reservation, owner inspection,
priority changes, explicit preemption requests, heartbeats, cooperative drain
callbacks, conflict inspection, and bounded quiescence waits. CPU and RAM
pressure includes every active owner class, including services.

GPU resources are populated from current `nvidia-smi` inventory and topology.
Compound bundles are derived from runtime NVLink, PCIe, and NUMA tags and
atomically reserve both physical accelerators and their declared interference
domains. Device indices and island membership are observations, not contracts.
