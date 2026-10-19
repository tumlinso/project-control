# Preserved internal Todo implementation

`todo_orchestrator-reference/` preserves the original internal engine source
from the pre-cutover Project Control snapshot. It is reference and rollback
material, excluded from the active import path and distribution. The original
runnable environment and authority backups are preserved separately.

Project Control now imports the independently installed `todo-orchestrator`
distribution through its public API.
