"""EAVT indexes — four covering indexes for O(log n) queries."""

from __future__ import annotations

from dataclasses import dataclass

from sortedcontainers import SortedList


@dataclass(frozen=True, slots=True, order=True)
class Datom:
    """A single fact: (entity, attribute, value, transaction). All strings for total ordering."""

    entity: str
    attribute: str
    value: str
    tx: str


@dataclass(frozen=True, slots=True, order=True)
class AEVTDatom:
    """AEVT ordering for 'all nodes with attribute Y' queries."""

    attribute: str
    entity: str
    value: str
    tx: str


@dataclass(frozen=True, slots=True, order=True)
class AVETDatom:
    """AVET ordering for 'nodes where attr=val' queries."""

    attribute: str
    value: str
    entity: str
    tx: str


@dataclass(frozen=True, slots=True, order=True)
class VAETDatom:
    """VAET ordering for 'all references to node X' queries."""

    value: str
    attribute: str
    entity: str
    tx: str


class EAVTIndex:
    """Maintains four SortedList indexes over datoms for O(log n) queries."""

    def __init__(self) -> None:
        self._eavt: SortedList[Datom] = SortedList()
        self._aevt: SortedList[AEVTDatom] = SortedList()
        self._avet: SortedList[AVETDatom] = SortedList()
        self._vaet: SortedList[VAETDatom] = SortedList()
        # Track datoms per entity for efficient retraction
        self._entity_datoms: dict[str, list[Datom]] = {}

    def add(self, datom: Datom) -> None:
        """Insert a datom into all four indexes."""
        self._eavt.add(datom)
        self._aevt.add(AEVTDatom(
            attribute=datom.attribute, entity=datom.entity,
            value=datom.value, tx=datom.tx,
        ))
        self._avet.add(AVETDatom(
            attribute=datom.attribute, value=datom.value,
            entity=datom.entity, tx=datom.tx,
        ))
        self._vaet.add(VAETDatom(
            value=datom.value, attribute=datom.attribute,
            entity=datom.entity, tx=datom.tx,
        ))
        self._entity_datoms.setdefault(datom.entity, []).append(datom)

    def retract_entity(self, entity: str) -> None:
        """Remove all datoms for a given entity from all four indexes."""
        datoms = self._entity_datoms.pop(entity, [])
        for datom in datoms:
            self._eavt.discard(datom)
            self._aevt.discard(AEVTDatom(
                attribute=datom.attribute, entity=datom.entity,
                value=datom.value, tx=datom.tx,
            ))
            self._avet.discard(AVETDatom(
                attribute=datom.attribute, value=datom.value,
                entity=datom.entity, tx=datom.tx,
            ))
            self._vaet.discard(VAETDatom(
                value=datom.value, attribute=datom.attribute,
                entity=datom.entity, tx=datom.tx,
            ))

    def entity_attrs(self, entity: str) -> list[Datom]:
        """All attributes of a given entity (EAVT prefix scan)."""
        lo = Datom(entity=entity, attribute="", value="", tx="")
        hi = Datom(entity=entity + "\uffff", attribute="", value="", tx="")
        return list(self._eavt.irange(lo, hi, (True, False)))

    def entity_attr(self, entity: str, attribute: str) -> list[Datom]:
        """Specific attribute of a given entity (EAVT prefix scan)."""
        lo = Datom(entity=entity, attribute=attribute, value="", tx="")
        hi = Datom(entity=entity, attribute=attribute + "\uffff", value="", tx="")
        return list(self._eavt.irange(lo, hi, (True, False)))

    def attr_entities(self, attribute: str) -> list[AEVTDatom]:
        """All entities with a given attribute (AEVT prefix scan)."""
        lo = AEVTDatom(attribute=attribute, entity="", value="", tx="")
        hi = AEVTDatom(attribute=attribute + "\uffff", entity="", value="", tx="")
        return list(self._aevt.irange(lo, hi, (True, False)))

    def attr_value(self, attribute: str, value: str) -> list[AVETDatom]:
        """Entities where attribute equals a specific value (AVET prefix scan)."""
        lo = AVETDatom(attribute=attribute, value=value, entity="", tx="")
        hi = AVETDatom(attribute=attribute, value=value + "\uffff", entity="", tx="")
        return list(self._avet.irange(lo, hi, (True, False)))

    def value_refs(self, value: str) -> list[VAETDatom]:
        """All entities referencing a given value (VAET prefix scan)."""
        lo = VAETDatom(value=value, attribute="", entity="", tx="")
        hi = VAETDatom(value=value + "\uffff", attribute="", entity="", tx="")
        return list(self._vaet.irange(lo, hi, (True, False)))

    def __len__(self) -> int:
        return len(self._eavt)
