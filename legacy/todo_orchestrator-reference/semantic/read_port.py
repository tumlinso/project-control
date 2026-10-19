"""Semantic namespace compatibility export for the canonical read port."""

from ..read_port import (
    READ_PORT_CAPABILITIES,
    READ_PORT_CONTRACT,
    READ_PORT_VERSION,
    TodoReadPort,
    create_read_port,
    create_todo_read_port,
)

__all__ = [
    "READ_PORT_CAPABILITIES",
    "READ_PORT_CONTRACT",
    "READ_PORT_VERSION",
    "TodoReadPort",
    "create_read_port",
    "create_todo_read_port",
]
