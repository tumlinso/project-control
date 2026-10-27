# Observer GPU policy at operator startup

> **Historical deployment recipe.** These examples were written for an
> installed release and `local-coding-worker` configuration. They are not the
> current PC service launcher instructions. For checkout-backed source runs,
> service restart, readiness, and rollback, use [deployment.md](deployment.md).
> Reconcile resource policy with the current supervisor configuration before
> applying this historical GPU UUID example; do not restore the retired worker
> config path or candidate wrapper.

The local operator can restrict the observer worker to approved GPU UUIDs before
starting the release launcher. This setting is not a public tool argument.
For the approved GPUs 1/3 in this qualification environment, a wrapper can use:

```sh
#!/bin/sh
export PROJECT_CONTROL_OBSERVER_GPU_UUIDS='["GPU-cf22c41f-5b58-77b1-3535-8fadd1ca6505","GPU-6c1cac7f-a360-0aef-ba98-2828bfd1db1a"]'
export PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR='/operator/private-observer-state'
exec /absolute/qualified-candidate/bin/project-control-release "$@"
```

Use the actual approved UUIDs and operator state/candidate paths for the host.
The UUID setting must be a nonempty JSON list of full `GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
UUID strings. Duplicate values are removed in supplied order; malformed input
rejects provider startup. Each provider snapshots this setting at construction,
so later environment changes do not widen its GPU policy. Restart the provider
through the normal service lifecycle to change that policy.

The provider remains lazy: discovery does not import the native backend, load a
model, reserve GPUs, or scan the model corpus. On first backend use it reads the
bound frozen `local-coding-worker/config/production-profile.toml`, retains its
model/server/storage settings, and passes the UUID restriction as
`deployment_policy.allowed_gpu_uuids` to `ProductionBackend`. The native backend
continues to use its canonical runtime and shared host admission, topology,
residency, and preemption checks. The private state root holds writable sidecars;
it does not replace global host resource admission. The qualified worker source
pin and release launcher bindings remain in force.

With the UUID setting absent, the native production profile remains unchanged.
The setting restricts eligible resources; it does not select a model, provide
resources, or start/download a model. Model choice remains the frozen production
profile's `compute_profiles` setting. These constructor tests use a native
constructor stub and perform no GPU operations; live resource preservation and
model qualification are separate release checks.
