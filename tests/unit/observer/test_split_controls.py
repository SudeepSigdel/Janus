"""D5: the rule that decides when a click target is emitted as its clickable children."""

from __future__ import annotations

from janus.observer.extract import split_children


def test_two_named_children_with_listeners_split_a_listenerless_container() -> None:
    assert split_children(False, [True, True], [-1, -1]) == [0, 1]


def test_a_container_with_its_own_listener_is_never_split() -> None:
    # navigate-tree: the folder <li> is a real control even though its children are too
    assert split_children(True, [True, True], [-1, -1]) == []


def test_one_clickable_child_is_not_enough() -> None:
    assert split_children(False, [True, False, False], [-1, -1, -1]) == []
    assert split_children(False, [False, False], [-1, -1]) == []


def test_text_only_descendants_without_listeners_are_dropped() -> None:
    # Reply span (listener) > div "Reply" (none); Forward span (listener) > div "Forward" (none)
    assert split_children(False, [True, False, True, False], [-1, 0, -1, 2]) == [0, 2]


def test_a_clickable_child_inside_another_clickable_child_is_part_of_it() -> None:
    assert split_children(False, [True, True, True], [-1, 0, -1]) == [0, 2]
    # outermost survives even through a non-clickable middle level
    assert split_children(False, [True, False, True, True], [-1, 0, 1, -1]) == [0, 3]
