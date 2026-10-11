"""Pure topology validation, stable identity and iterative traversal."""

import pytest

from custom_components.jai_lighting.models import Group, Identity, Leaf, ModelError
from custom_components.jai_lighting.topology import Topology


def leaf(id_, parent, identity=None):
    return Leaf(id_, Identity("registry", identity or id_), parent)


def test_nested_resolver_and_paths():
    tree = Topology(
        [Group("r", "Root"), Group("a", "Child", "r")], [leaf("one", "r"), leaf("two", "a")]
    )
    assert tree.resolve("r") == ("one", "two")
    assert tree.resolve("a") == ("two",)
    assert tree.leaf_path("two") == ("r", "a", "two")
    with pytest.raises(TypeError):
        tree.groups["new"] = Group("new", "Invalid")


@pytest.mark.parametrize(
    "groups",
    [
        [Group("a", "A", "a")],
        [Group("a", "A", "b"), Group("b", "B", "a")],
        [Group("a", "A", "missing")],
        [Group("a", "A"), Group("a", "Duplicate")],
    ],
)
def test_invalid_group_topology(groups):
    with pytest.raises(ModelError):
        Topology(groups)


def test_physical_single_parent_and_alias_prevention():
    groups = [Group("a", "A"), Group("b", "B")]
    with pytest.raises(ModelError, match="Duplicate physical"):
        Topology(groups, [leaf("one", "a", "device"), leaf("two", "b", "device")])
    with pytest.raises(ModelError, match="Duplicate topology"):
        Topology(groups, [leaf("one", "a"), leaf("one", "b")])
    tree = Topology(groups, [leaf("one", "a"), leaf("two", "b")])
    with pytest.raises(ModelError, match="alias collision"):
        tree.validate_routing({"one": "light.sample", "two": "light.sample"})


def test_missing_leaf_parent_and_non_light_fallback():
    with pytest.raises(ModelError):
        Topology([], [leaf("one", "missing")])
    with pytest.raises(ModelError):
        Identity("entity_id_fallback", "switch.sample")


def test_deep_tree_empty_group_and_identity_survive_rename():
    groups = [Group(str(i), "User name", str(i - 1) if i else None) for i in range(1500)]
    tree = Topology(groups, [leaf("one", "1499")])
    assert tree.resolve("0") == ("one",)
    assert len(tree.leaf_path("one")) == 1501
    assert Topology([Group("a", "Empty")]).resolve("a") == ()
    tree.validate_routing({"one": "light.renamed"})
    assert tree.leaves["one"].identity.value == "one"
