# Shared interactive shell environment. Source from the user's shell profile.
case "${PATH:-}" in
    "${HOME}/.local/bin"|"${HOME}/.local/bin":*) ;;
    *) export PATH="${HOME}/.local/bin${PATH:+:${PATH}}" ;;
esac
export PROJECT_CONTROL_RUNTIME_PYTHON="${HOME}/.local/share/project-control/current/bin/python"
