"""Errors raised by the memory store, with the codes of docs/contracts/memory.md §3.4.

They are not SensaiError: the tool layer (MEM-03) turns them into a tool result in
error, sent back to the model so that it can fix its call. They never reach the user
as an `error` event.
"""

from __future__ import annotations


class MemoryStoreError(Exception):
    """Base class: a stable lowercase `code` and a French message for the model."""

    code = "memory_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFound(MemoryStoreError):
    """No entry at (kind, ref)."""

    code = "not_found"

    def __init__(self, kind: str, ref: str) -> None:
        super().__init__(f"Aucune entrée {kind} « {ref} » en mémoire.")
        self.kind = kind
        self.ref = ref


class EntityNotFound(MemoryStoreError):
    """A designation (name, alias or ref) matches no entity."""

    code = "entity_not_found"

    def __init__(self, designation: str) -> None:
        super().__init__(f"Aucune entité ne correspond à « {designation} ».")
        self.designation = designation


class AmbiguousEntity(MemoryStoreError):
    """A designation matches several entities; candidates are (type, name, ref)."""

    code = "ambiguous_entity"

    def __init__(self, designation: str, candidates: list[tuple[str, str, str]]) -> None:
        listed = ", ".join(
            f"{name} ({entity_type}, ref {ref})" for entity_type, name, ref in candidates
        )
        super().__init__(
            f"« {designation} » désigne plusieurs entités : {listed}. Précisez la ref."
        )
        self.designation = designation
        self.candidates = candidates


class InvalidPatch(MemoryStoreError):
    """Unknown or read-only field, or a value refused by a constraint (§3.4)."""

    code = "invalid_patch"


class Conflict(MemoryStoreError):
    """Uniqueness violation: name already taken, relation already present."""

    code = "conflict"
