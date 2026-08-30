# PCU-INSTALL-CUTOVER/1

Installation builds an isolated candidate environment containing both local distributions, verifies observer HTTP and Codex stdio using official MCP clients, checks exact tool names and schemas, and proves one Todo authority. It captures the old service unit, executable, environment source, registration, configuration hashes, and rollback commands before change.

No live change occurs during repository implementation or bootstrap. Final cutover is a digest-checked atomic swap: register only `project-control` for Codex, retain the observer endpoint and tunnel behavior, verify health and shared revision visibility, and automatically restore the old registration and service on failure. The compatibility alias is not concurrently registered. Old packages, service checkout, standalone repository, historical worktrees, and state remain until a later explicit cleanup campaign.
