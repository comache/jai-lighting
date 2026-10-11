"""Explicit cross-hierarchy collections; no dynamic label membership or masters."""

from dataclasses import dataclass

from .models import ModelError


@dataclass(frozen=True)
class Collection:
    id: str
    name: str
    members: tuple[str, ...] = ()

    def __post_init__(self):
        if (
            not isinstance(self.id, str)
            or not self.id
            or not isinstance(self.name, str)
            or not self.name.strip()
            or not isinstance(self.members, tuple)
            or len(self.members) > 10000
            or any(not isinstance(id_, str) or not id_ for id_ in self.members)
            or len(set(self.members)) != len(self.members)
        ):
            raise ModelError("Invalid collection identity, name or duplicate membership")
