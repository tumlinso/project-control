#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
python="$repo_root/.dev-venv/bin/python"
skills_root="$(cd "$repo_root/../.agents/skills" 2>/dev/null && pwd -P)" || {
  echo "canonical Project Control Skills source is unavailable" >&2
  exit 2
}
todo_root="$skills_root/todo-orchestrator"

if [[ ! -x "$python" ]]; then
  echo "source-test interpreter is unavailable: $python" >&2
  exit 2
fi
if [[ ! -f "$todo_root/scripts/todo.py" || ! -d "$todo_root/todo_orchestrator" ]]; then
  echo "canonical Todo supplier source is unavailable: $todo_root" >&2
  exit 2
fi
if [[ $# -eq 0 ]]; then
  echo "usage: $0 <python arguments>" >&2
  echo "example: $0 -m unittest tests.assistance.test_lab -v" >&2
  exit 2
fi

# Force source mode with the canonical Skills supplier. Inherited release pins
# can redirect source tests into the installed release; inherited Python path
# settings can shadow either source tree. Runtime identity checks remain active.
unset PYTHONHOME PYTHONPATH
unset PROJECT_CONTROL_RELEASE_MANIFEST PROJECT_CONTROL_RELEASE_DIGEST
unset CODING_WORKFLOW_SKILLS_ROOT
export PROJECT_CONTROL_SKILLS_ROOT="$skills_root"
export PYTHONPATH="$todo_root:$repo_root/src"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1

cd "$repo_root"
exec "$python" "$@"
