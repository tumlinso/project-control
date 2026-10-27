# Candidate delivery and production deployment

Building and publishing this overhaul does not deploy it. Existing services and selected releases remain untouched until a separate production cutover.

## Implementation delivery

A delivery contains independently buildable applications/shared packages, review branches, tested source identities, known limits, archive provenance and candidate launch instructions. Compatibility readers are tested against copied historical stores.

Candidates use private configuration, state, caches and artifacts, and separate ports. Installing development wheels does not authorize installing over a live application.

## Existing runtime

The established host deployment selects a sealed release through `~/.local/state/project-control/current`. Its executable package, standalone Todo package and launch configuration are distinct from this source checkout. Keep the release immutable and preserve rollback information.

This overhaul must not change that pointer, systemd units, launchers, registered credentials, installed skills, live databases or scheduler authority. It must not start a replacement allocator or inference service.

## A later cutover

A future deployment identifies exact application/dependency versions, preserves authority state and checks actual client transports. Importing production source/index state and historical data requires an explicit compatibility decision. Rollback changes executable selection and launch configuration; it does not rewind task, experiment or scheduler authority state.

The former inference-era deployment guide is preserved at `archive/assistance-v0/docs/deployment.md` as historical material, not current instructions.
