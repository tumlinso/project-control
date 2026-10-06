# Persistent model cache

Cold canonical assets remain under `/mnt/block/core4-models`. Production uses
the persistent SSD cache at `/home/tumlinson/.local/share/core4/models`:

```text
<candidate-id>/<payload-sha256>/
  model.gguf
  asset-manifest.json
  READY
active-profile.json
```

`local-worker model-cache inspect|list|install|verify|activate|remove` is the
public surface. Install holds a cache lock, checks the pinned source manifest
and free-space margin, copies into a same-filesystem partial directory, hashes
the copied payload, fsyncs durable files, and atomically renames it. Models are
not deleted after tasks.

The production profile maps `narrow` statically to the cached
Qwen3-Coder-30B-A3B Q4_K_M candidate on one two-GPU island. Project Control
observer inquiries explicitly request `narrow`; two observer servers can use
separate two-GPU islands concurrently under that profile. `wide` has no
production mapping and is
disabled by default. The Qwen3-Coder-Next Q4_K_M candidate remains listed as a
durable candidate, but is not selected by a compute profile. Slots are reusable
only when both candidate identity and compute profile match. Switching
profiles evicts and reloads an idle incompatible slot; an actively
leased/generating slot is never evicted. Observer investigations may explicitly
override llama.cpp split mode with `layer` or `tensor` for diagnostics on the
narrow profile. The resolved split participates in compatibility, so an idle
mismatch reloads. All llama services enable CUDA P2P and preserve
runtime-discovered NVLink-pair adjacency.

Ordinary use performs quick READY, schema, size, immutable-path, GGUF-header,
inode, and mtime checks. Install, explicit `verify --full`, and metadata change
perform full SHA-256 verification. The active-profile pointer is atomic and
outside payload directories. Removal refuses active or leased payloads.
