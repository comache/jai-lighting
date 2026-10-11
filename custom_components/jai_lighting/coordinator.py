"""Per-group views of the shared manager; no independent leaf memories."""

import asyncio

from homeassistant.core import callback

from .engine import Member
from .manager import Manager
from .membership import identity_for
from .models import Group, Leaf
from .storage import Snapshot
from .topology import Topology


class FaderView:
    """Compatibility/read interface backed entirely by shared retained state."""

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator

    @property
    def master(self):
        return self.coordinator.manager.snapshot.runtime.masters.get(self.coordinator.group_id, 255)

    @master.setter
    def master(self, value):
        # Isolated adapter fixtures only; public actions persist before acceptance.
        self.coordinator.manager.snapshot.runtime.masters[self.coordinator.group_id] = value

    @property
    def members(self):
        c = self.coordinator
        routes = c.manager.routes()
        return tuple(routes[id_] or id_ for id_ in c.manager.snapshot.tree.resolve(c.group_id))

    @property
    def memory(self):
        c = self.coordinator
        routes = c.manager.routes()
        return {
            routes[id_] or id_: value
            for id_, value in c.manager.snapshot.runtime.targets.items()
            if id_ in c.manager.snapshot.tree.resolve(c.group_id)
        }

    def as_dict(self):
        return {"version": 2, "master": self.master, "memory": self.memory}


class GroupCoordinator:
    def __init__(self, hass, group_or_members, manager_or_lock) -> None:
        if isinstance(manager_or_lock, Manager):
            self.manager, self.group_id = manager_or_lock, group_or_members
        else:
            # Preserve the existing isolated physical-adapter regression harness.
            self.manager = Manager(hass, manager_or_lock or asyncio.Lock())
            self.manager.standalone = True
            self.group_id = "adapter-reference"
            self.manager.repo.snapshot = Snapshot(
                Topology(
                    [Group(self.group_id, "Adapter reference")],
                    [
                        Leaf(id_, identity_for(hass, id_, existing=True), self.group_id)
                        for id_ in dict.fromkeys(group_or_members)
                    ],
                )
            )
        self.engine = FaderView(self)
        self.lock = self.manager.lock
        self._unsubscribe = None

    @property
    def failures(self):
        return self.manager.failures

    def observations(self):
        routes, observations = self.manager.routes(), self.manager.observations()
        return {
            routes[id_] or id_: Member(o.available, o.on, o.capabilities.dimmable, o.brightness)
            for id_ in self.manager.snapshot.tree.resolve(self.group_id)
            for o in [observations[id_]]
        }

    @property
    def available(self):
        return any(o.available for o in self.observations().values())

    @property
    def is_on(self):
        return any(o.available and o.on for o in self.observations().values())

    @callback
    def start(self, listener):
        self.manager.listeners[self.group_id] = listener
        self._unsubscribe = lambda: self.manager.listeners.pop(self.group_id, None)

    @callback
    def stop(self):
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    async def execute(self, operation, context, master=None, fields=None):
        return await self.manager.execute(
            operation, context, groups=(self.group_id,), master=master, fields=fields
        )
