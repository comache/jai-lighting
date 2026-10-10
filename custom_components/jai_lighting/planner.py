"""Pure hierarchical fader planning; never call virtual child services."""

from collections.abc import Mapping

from .engine import level
from .models import Intention, ModelError, Observation, Plan, PowerPolicy, RuntimeState
from .topology import Topology


def observe(state: RuntimeState, observations: Mapping[str, Observation]) -> None:
    """Only actual ON brightness supersedes a bounded retained target."""
    for leaf_id, observation in observations.items():
        if observation.available and observation.on and observation.brightness is not None:
            state.targets[leaf_id] = observation.brightness


def bootstrap(state: RuntimeState, leaf_id: str, observation: Observation) -> None:
    if observation.available and observation.capabilities.dimmable:
        state.targets.setdefault(leaf_id, observation.brightness or 255)


def relative(
    tree: Topology,
    state: RuntimeState,
    observations: Mapping[str, Observation],
    group_id: str,
    master: int,
) -> Plan:
    if type(master) is not int or not 3 <= master <= 255:
        raise ModelError("Master must be an integer in 3..255")
    ids = tree.resolve(group_id)
    result = Plan(state.copy())
    subset = {id_: observations.get(id_, Observation()) for id_ in ids}
    observe(result.state, subset)
    old = result.state.masters.get(group_id, 255)
    result.state.masters[group_id] = master
    if old == master:
        return result
    factor = master / old
    active = any(o.available and o.on for o in subset.values())
    for id_, o in subset.items():
        if not active:
            bootstrap(result.state, id_, o)
            if id_ in result.state.targets:
                result.state.targets[id_] = level(result.state.targets[id_] * factor)
        elif o.available and o.on and o.capabilities.dimmable and o.brightness is not None:
            brightness = level(o.brightness * factor)
            if brightness != o.brightness:
                result.commands[id_] = Intention(id_, True, {"brightness": brightness})
        elif not o.available:
            result.skipped[id_] = [o.reason]
        elif o.on and o.capabilities.dimmable and o.brightness is None:
            result.skipped[id_] = ["missing_brightness"]
    return result


def power(
    tree: Topology,
    state: RuntimeState,
    observations: Mapping[str, Observation],
    group_id: str,
    on: bool,
    master: int | None = None,
    explicit_properties: bool = False,
) -> Plan:
    ids = tree.resolve(group_id)
    result = (
        relative(tree, state, observations, group_id, master)
        if on and master is not None
        else Plan(state.copy())
    )
    subset = {id_: observations.get(id_, Observation()) for id_ in ids}
    observe(result.state, subset)
    full = (
        on
        and master is None
        and not explicit_properties
        and (tree.groups[group_id].policy == PowerPolicy.FULL)
    )
    for id_, o in subset.items():
        if not o.available:
            result.skipped[id_] = [o.reason]
            continue
        bootstrap(result.state, id_, o)
        if not on:
            result.commands[id_] = Intention(id_, False)
        elif full or not o.on:
            fields = {}
            if o.capabilities.dimmable:
                fields["brightness"] = 255 if full else result.state.targets.get(id_, 255)
            result.commands[id_] = Intention(id_, True, fields)
    return result
