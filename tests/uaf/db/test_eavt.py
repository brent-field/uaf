"""Tests for EAVT indexes."""

from __future__ import annotations

from uaf.db.eavt import Datom, EAVTIndex


def _datom(entity: str, attribute: str, value: str, tx: str = "tx1") -> Datom:
    return Datom(entity=entity, attribute=attribute, value=value, tx=tx)


class TestAdd:
    def test_add_increments_length(self) -> None:
        idx = EAVTIndex()
        assert len(idx) == 0
        idx.add(_datom("e1", "type", "artifact"))
        assert len(idx) == 1

    def test_add_multiple(self) -> None:
        idx = EAVTIndex()
        idx.add(_datom("e1", "type", "artifact"))
        idx.add(_datom("e1", "title", "My Doc"))
        idx.add(_datom("e2", "type", "paragraph"))
        assert len(idx) == 3


class TestEntityAttrs:
    def test_returns_all_attrs_for_entity(self) -> None:
        idx = EAVTIndex()
        idx.add(_datom("e1", "type", "artifact"))
        idx.add(_datom("e1", "title", "Report"))
        idx.add(_datom("e2", "type", "paragraph"))

        results = idx.entity_attrs("e1")
        assert len(results) == 2
        entities = {d.entity for d in results}
        assert entities == {"e1"}

    def test_empty_for_missing_entity(self) -> None:
        idx = EAVTIndex()
        idx.add(_datom("e1", "type", "artifact"))
        assert idx.entity_attrs("e999") == []


class TestEntityAttr:
    def test_returns_specific_attr(self) -> None:
        idx = EAVTIndex()
        idx.add(_datom("e1", "type", "artifact"))
        idx.add(_datom("e1", "title", "Report"))

        results = idx.entity_attr("e1", "title")
        assert len(results) == 1
        assert results[0].value == "Report"


class TestAttrEntities:
    def test_returns_all_entities_with_attr(self) -> None:
        idx = EAVTIndex()
        idx.add(_datom("e1", "type", "artifact"))
        idx.add(_datom("e2", "type", "paragraph"))
        idx.add(_datom("e3", "title", "X"))

        results = idx.attr_entities("type")
        assert len(results) == 2
        entities = {d.entity for d in results}
        assert entities == {"e1", "e2"}


class TestAttrValue:
    def test_returns_entities_matching_value(self) -> None:
        idx = EAVTIndex()
        idx.add(_datom("e1", "type", "artifact"))
        idx.add(_datom("e2", "type", "paragraph"))
        idx.add(_datom("e3", "type", "artifact"))

        results = idx.attr_value("type", "artifact")
        assert len(results) == 2
        entities = {d.entity for d in results}
        assert entities == {"e1", "e3"}

    def test_no_match(self) -> None:
        idx = EAVTIndex()
        idx.add(_datom("e1", "type", "artifact"))
        assert idx.attr_value("type", "nonexistent") == []


class TestValueRefs:
    def test_returns_refs(self) -> None:
        idx = EAVTIndex()
        idx.add(_datom("e1", "ref_target", "e5"))
        idx.add(_datom("e2", "ref_target", "e5"))
        idx.add(_datom("e3", "ref_target", "e6"))

        results = idx.value_refs("e5")
        assert len(results) == 2
        entities = {d.entity for d in results}
        assert entities == {"e1", "e2"}


class TestRetractEntity:
    def test_retract_removes_from_all_indexes(self) -> None:
        idx = EAVTIndex()
        idx.add(_datom("e1", "type", "artifact"))
        idx.add(_datom("e1", "title", "Report"))
        idx.add(_datom("e2", "type", "paragraph"))

        idx.retract_entity("e1")

        assert len(idx) == 1
        assert idx.entity_attrs("e1") == []
        assert len(idx.attr_entities("type")) == 1  # only e2 remains
        assert idx.attr_value("type", "artifact") == []

    def test_retract_nonexistent_is_noop(self) -> None:
        idx = EAVTIndex()
        idx.add(_datom("e1", "type", "artifact"))
        idx.retract_entity("e999")
        assert len(idx) == 1


class TestScaleTest:
    def test_1000_datoms(self) -> None:
        idx = EAVTIndex()
        for i in range(1000):
            idx.add(_datom(f"e{i}", "type", f"type_{i % 10}", tx=f"tx{i}"))

        assert len(idx) == 1000

        # Prefix scan should return ~100 results for each type
        results = idx.attr_value("type", "type_0")
        assert len(results) == 100

        # Entity lookup — "e42" also prefix-matches "e420"-"e429"
        # so we expect 11 results (e42 + e420..e429)
        results_e = idx.entity_attrs("e42")
        assert len(results_e) == 11

        # Exact entity lookup with unique id
        results_unique = idx.entity_attrs("e999")
        assert len(results_unique) == 1
