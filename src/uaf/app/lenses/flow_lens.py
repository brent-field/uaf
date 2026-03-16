"""FlowLens — project management with Gantt, Dependency, DAG, and Kanban views."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from html import escape
from typing import TYPE_CHECKING

from uaf.app.lenses import LensView
from uaf.app.lenses.actions import (
    CreateTask,
    CreateTaskGroup,
    DeleteNode,
    RemoveDependency,
    RenameArtifact,
    ReorderNodes,
    SetDateRange,
    SetDependency,
    SetDueDate,
    SetTaskStatus,
    ToggleTask,
    UpdateTask,
)
from uaf.core.edges import Edge, EdgeType
from uaf.core.node_id import EdgeId, utc_now
from uaf.core.nodes import (
    Artifact,
    NodeType,
    Task,
    make_node_metadata,
)
from uaf.core.operations import ReorderChildren

if TYPE_CHECKING:
    from uaf.app.lenses.actions import LensAction
    from uaf.core.node_id import NodeId
    from uaf.security.secure_graph_db import SecureGraphDB, Session

_SUPPORTED = frozenset({NodeType.ARTIFACT, NodeType.TASK})

_STATUS_ORDER: tuple[str, ...] = (
    "todo", "in_progress", "blocked", "done", "dropped",
)
VALID_STATUSES = frozenset(_STATUS_ORDER)

# Status cycle for ToggleTask
_STATUS_CYCLE = {"todo": "in_progress", "in_progress": "done", "done": "todo"}
_STATUS_ICONS = {
    "todo": "&#9744;", "in_progress": "&#9634;", "done": "&#9745;",
    "blocked": "&#9888;", "dropped": "&#10005;",
}
_STATUS_LABELS: dict[str, str] = {
    "todo": "To Do", "in_progress": "In Progress", "blocked": "Blocked",
    "done": "Done", "dropped": "Dropped",
}
_TOGGLE_LABELS = {
    "todo": "Start &#x25B6;",
    "in_progress": "Done &#x2713;",
    "done": "Reopen &#x21A9;",
    "blocked": "Reopen &#x21A9;",
    "dropped": "Reopen &#x21A9;",
}


def _status_icon(status: str) -> str:
    return _STATUS_ICONS.get(status, "&#9744;")


def _empty_state(hint: str) -> str:
    return (
        '<div class="flow-empty">'
        "<p>No tasks yet</p>"
        f'<p class="flow-empty-hint">{hint}</p>'
        "</div>"
    )


class FlowLens:
    """Project management lens with Gantt, dependency, DAG, and Kanban views."""

    @property
    def lens_type(self) -> str:
        return "flow"

    @property
    def supported_node_types(self) -> frozenset[NodeType]:
        return _SUPPORTED

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render(
        self, db: SecureGraphDB, session: Session, artifact_id: NodeId,
        *, mode: str = "list",
    ) -> LensView:
        """Render the project artifact. Default view is Gantt."""
        artifact = db.get_node(session, artifact_id)
        if artifact is None or not isinstance(artifact, Artifact):
            return LensView(
                lens_type="flow",
                artifact_id=artifact_id,
                title="(not found)",
                content="",
                content_type="text/html",
                node_count=0,
                rendered_at=utc_now(),
            )

        tasks = db.get_children(session, artifact_id)
        task_list = [t for t in tasks if isinstance(t, Task)]

        match mode:
            case "gantt":
                content = self._render_gantt(task_list, artifact_id, db, session)
            case "list":
                content = self._render_list(task_list, artifact_id, db, session)
            case "deps":
                content = self._render_deps(task_list, artifact_id, db, session)
            case "dag":
                content = self._render_dag(task_list, artifact_id, db, session)
            case "kanban":
                content = self._render_kanban(task_list, artifact_id)
            case _:
                content = self._render_gantt(task_list, artifact_id, db, session)

        return LensView(
            lens_type="flow",
            artifact_id=artifact_id,
            title=artifact.title,
            content=content,
            content_type="text/html",
            node_count=1 + len(task_list),
            rendered_at=utc_now(),
        )

    # ------------------------------------------------------------------
    # View renderers
    # ------------------------------------------------------------------

    def _render_gantt(
        self, tasks: list[Task], artifact_id: NodeId,
        db: SecureGraphDB, session: Session,
    ) -> str:
        """Render Gantt view — task list left, bars/milestones right."""
        if not tasks:
            return _empty_state(
                "Click <strong>+ Task</strong> above to add your first task,"
                " then drag between tasks to set dependencies."
            )

        # Build dependency map for inline display
        task_map = {t.meta.id: t for t in tasks}
        dep_names: dict[NodeId, list[str]] = {}
        for task in tasks:
            edges = db._db.get_edges_from(task.meta.id)
            targets = [
                escape(task_map[e.target].title)
                for e in edges
                if e.edge_type == EdgeType.DEPENDS_ON and e.target in task_map
            ]
            dep_names[task.meta.id] = targets

        rows: list[str] = []
        for task in tasks:
            name = escape(task.title)
            nid = task.meta.id
            status_cls = f"status-{task.status}"

            # Dependency subtitle
            dep_list = dep_names.get(nid, [])
            dep_sub = ""
            if dep_list:
                dep_sub = (
                    f'<span class="task-dep-info">depends on: '
                    f'{", ".join(dep_list)}</span>'
                )

            # Left side: task name + drag handle + dep info
            left = (
                f'<td class="task-name {status_cls}">'
                f'<span class="drag-handle" title="Drag to link dependency">'
                f"&#x1f517;</span>"
                f"{name}{dep_sub}</td>"
            )

            # Right side: bar or milestone
            if task.start_date and task.end_date:
                bar = (
                    f'<div class="gantt-bar {status_cls}" '
                    f'data-start="{task.start_date.isoformat()}" '
                    f'data-end="{task.end_date.isoformat()}">'
                    f"</div>"
                )
                right = f'<td class="gantt-cell">{bar}</td>'
            elif task.due_date:
                right = (
                    f'<td class="gantt-cell">'
                    f'<div class="gantt-milestone" '
                    f'data-date="{task.due_date.isoformat()}"></div>'
                    f"</td>"
                )
            else:
                right = '<td class="gantt-cell"></td>'

            actions = (
                f'<td class="task-actions">'
                f'<button class="btn btn-sm"'
                f' hx-post="/artifacts/{artifact_id}/flow/toggle-task"'
                f" hx-vals='{{\"node_id\": \"{nid}\"}}'"
                f' hx-target="#flow-content"'
                f' hx-swap="innerHTML"'
                f' title="Toggle status">'
                f"{_status_icon(task.status)}"
                f"</button>"
                f'<button class="btn btn-sm btn-danger"'
                f' hx-post="/artifacts/{artifact_id}/flow/delete-task"'
                f" hx-vals='{{\"node_id\": \"{nid}\"}}'"
                f' hx-target="#flow-content"'
                f' hx-swap="innerHTML"'
                f' hx-confirm="Delete task?"'
                f' title="Delete">&times;</button>'
                f"</td>"
            )
            rows.append(f'<tr data-node-id="{nid}">{left}{right}{actions}</tr>')

        # Compute timeline range from all task dates
        all_dates: list[datetime] = []
        for t in tasks:
            if t.start_date is not None:
                all_dates.append(
                    t.start_date if isinstance(t.start_date, datetime)
                    else datetime.combine(t.start_date, datetime.min.time())
                )
            if t.end_date is not None:
                all_dates.append(
                    t.end_date if isinstance(t.end_date, datetime)
                    else datetime.combine(t.end_date, datetime.min.time())
                )
            if t.due_date is not None:
                all_dates.append(
                    t.due_date if isinstance(t.due_date, datetime)
                    else datetime.combine(t.due_date, datetime.min.time())
                )

        if all_dates:
            min_date = min(all_dates) - timedelta(days=1)
            max_date = max(all_dates) + timedelta(days=1)
        else:
            now = datetime.now()
            min_date = now
            max_date = now + timedelta(days=30)

        # Generate ~5 evenly-spaced date labels
        total_days = (max_date - min_date).total_seconds()
        date_labels: list[tuple[float, str]] = []
        num_labels = 5
        for i in range(num_labels):
            frac = i / (num_labels - 1) if num_labels > 1 else 0.0
            d = min_date + timedelta(seconds=frac * total_days)
            pct = frac * 100
            label = d.strftime("%b %-d")
            date_labels.append((pct, label))

        timeline_header = (
            '<th class="gantt-timeline-header"><div class="gantt-date-labels">'
            + "".join(
                f'<span class="gantt-date-label" style="left:{pct}%">'
                f"{label}</span>"
                for pct, label in date_labels
            )
            + "</div></th>"
        )

        return (
            f'<table class="flow-gantt"'
            f' data-timeline-start="{min_date.isoformat()}"'
            f' data-timeline-end="{max_date.isoformat()}">'
            f"<thead><tr><th>Task</th>{timeline_header}"
            f"<th></th></tr></thead>"
            f'<tbody>{"".join(rows)}</tbody>'
            f"</table>"
        )

    def _render_list(
        self, tasks: list[Task], artifact_id: NodeId,
        db: SecureGraphDB, session: Session,
    ) -> str:
        """Render List view — data-grid with full keyboard navigation."""
        aid = artifact_id
        hx = (
            ' hx-target="#flow-content" hx-swap="innerHTML"'
            ' hx-sync="closest table:replace"'
        )

        # Build dependency map for inline display
        task_map = {t.meta.id: t for t in tasks}
        dep_map: dict[NodeId, list[tuple[NodeId, str]]] = {}
        for task in tasks:
            edges = db._db.get_edges_from(task.meta.id)
            dep_map[task.meta.id] = [
                (e.target, escape(task_map[e.target].title))
                for e in edges
                if e.edge_type == EdgeType.DEPENDS_ON and e.target in task_map
            ]

        rows: list[str] = []
        for ri, task in enumerate(tasks):
            name = escape(task.title)
            nid = task.meta.id
            status_cls = f"status-{task.status}"
            vals = f'{{"node_id":"{nid}","mode":"list"}}'

            # Col 0: status toggle
            c0 = (
                f'<td><button data-row="{ri}" data-col="0"'
                f' class="gc list-check {status_cls}"'
                f' hx-post="/artifacts/{aid}/flow/toggle-task"'
                f" hx-vals='{vals}'{hx}"
                f' data-action="toggle">'
                f"{_status_icon(task.status)}</button></td>"
            )

            # Col 1: status dropdown
            options = "".join(
                f'<option value="{s}"'
                f'{" selected" if s == task.status else ""}>'
                f"{_STATUS_LABELS[s]}</option>"
                for s in _STATUS_LABELS
            )
            c1 = (
                f'<td><select data-row="{ri}" data-col="1"'
                f' class="gc list-status-select {status_cls}"'
                f' name="status"'
                f' hx-post="/artifacts/{aid}/flow/set-status"'
                f" hx-vals='{vals}'"
                f' hx-trigger="change"'
                f"{hx}>"
                f"{options}</select></td>"
            )

            # Col 2: title
            c2 = (
                f'<td><input data-row="{ri}" data-col="2"'
                f' class="gc list-cell-input {status_cls}"'
                f' type="text" value="{name}" name="title"'
                f' hx-post="/artifacts/{aid}/flow/update-task"'
                f' hx-include="closest tr"'
                f' hx-trigger="save"{hx}'
                f' data-action="update" /></td>'
            )

            # Col 3: start date
            sd = (
                task.start_date.strftime("%Y-%m-%d")
                if task.start_date else ""
            )
            c3 = (
                f'<td><input data-row="{ri}" data-col="3"'
                f' class="gc list-cell-date"'
                f' type="date" value="{sd}" name="start_date"'
                f' hx-post="/artifacts/{aid}/flow/update-task-dates"'
                f' hx-include="closest tr"'
                f' hx-trigger="save"{hx}'
                f' data-action="update" /></td>'
            )

            # Col 4: end date
            ed = (
                task.end_date.strftime("%Y-%m-%d")
                if task.end_date else ""
            )
            c4 = (
                f'<td><input data-row="{ri}" data-col="4"'
                f' class="gc list-cell-date"'
                f' type="date" value="{ed}" name="end_date"'
                f' hx-post="/artifacts/{aid}/flow/update-task-dates"'
                f' hx-include="closest tr"'
                f' hx-trigger="save"{hx}'
                f' data-action="update" /></td>'
            )

            # Col 5: deps
            dep_list = dep_map.get(nid, [])
            dep_chips: list[str] = []
            for dep_id, dep_name in dep_list:
                dep_chips.append(
                    f'<span class="dep-chip">'
                    f"&#8594; {dep_name}"
                    f'<button class="dep-remove" '
                    f'hx-post="/artifacts/{aid}/flow/remove-dependency"'
                    f" hx-vals='{{\"source_id\": \"{nid}\","
                    f' "target_id": "{dep_id}"}}\''
                    f' hx-target="#flow-content" hx-swap="innerHTML"'
                    f' title="Remove">&times;</button>'
                    f"</span>"
                )
            dep_html = " ".join(dep_chips)
            c5 = f'<td class="dep-arrows">{dep_html}</td>'

            # Col 6: delete
            c6 = (
                f'<td><button data-row="{ri}" data-col="6"'
                f' class="gc list-row-delete"'
                f' hx-post="/artifacts/{aid}/flow/delete-task"'
                f" hx-vals='{vals}'{hx}"
                f' hx-confirm="Delete task?"'
                f' data-action="delete">&times;</button></td>'
            )

            hidden = (
                f'<td class="list-hidden">'
                f'<input type="hidden" name="node_id" value="{nid}" />'
                f'<input type="hidden" name="mode" value="list" />'
                f"</td>"
            )

            rows.append(
                f'<tr data-node-id="{nid}">'
                f"{c0}{c1}{c2}{c3}{c4}{c5}{c6}{hidden}</tr>"
            )

        # New-row placeholder
        nr = len(tasks)
        new_hx = (
            f' hx-post="/artifacts/{aid}/flow/create-task"'
            f' hx-include="closest tr"'
            f" hx-vals='{{\"mode\":\"list\"}}'"
            f' hx-trigger="keydown[key==\'Enter\']"'
            f"{hx}"
            f' data-action="create"'
        )
        new_row = (
            f'<tr class="list-row-new">'
            f"<td></td>"
            f"<td></td>"
            f'<td><input data-row="{nr}" data-col="2"'
            f' class="gc list-cell-input"'
            f' type="text" name="title" placeholder="Add a task\u2026"'
            f"{new_hx} /></td>"
            f'<td><input data-row="{nr}" data-col="3"'
            f' class="gc list-cell-date"'
            f' type="date" name="start_date"'
            f"{new_hx} /></td>"
            f'<td><input data-row="{nr}" data-col="4"'
            f' class="gc list-cell-date"'
            f' type="date" name="end_date"'
            f"{new_hx} /></td>"
            f"<td></td>"
            f"<td></td>"
            f"</tr>"
        )

        return (
            '<table class="flow-list-grid" role="grid">'
            "<thead><tr>"
            '<th class="col-status"></th>'
            '<th class="col-status">Status</th>'
            "<th>Task</th>"
            '<th class="col-date">Start</th>'
            '<th class="col-date">End</th>'
            "<th>Deps</th>"
            '<th class="col-actions"></th>'
            "</tr></thead>"
            f'<tbody>{"".join(rows)}{new_row}</tbody>'
            "</table>"
        )

    def _render_deps(
        self, tasks: list[Task], artifact_id: NodeId,
        db: SecureGraphDB, session: Session,
    ) -> str:
        """Render dependency view — tasks as rows with arrows for DEPENDS_ON edges."""
        if not tasks:
            return _empty_state(
                "Click <strong>+ Task</strong> above to add your first task,"
                " then drag between tasks to set dependencies."
            )

        # Build dependency map: task_id -> list of tasks it depends on
        deps: dict[NodeId, list[NodeId]] = {}
        for task in tasks:
            edges = db._db.get_edges_from(task.meta.id)
            dep_targets = [
                e.target for e in edges if e.edge_type == EdgeType.DEPENDS_ON
            ]
            deps[task.meta.id] = dep_targets

        task_map = {t.meta.id: t for t in tasks}
        rows: list[str] = []
        for task in tasks:
            name = escape(task.title)
            nid = task.meta.id
            dep_list = deps.get(nid, [])
            dep_str = ", ".join(str(d) for d in dep_list) if dep_list else "none"

            # Show dependency names with remove buttons
            dep_chips: list[str] = []
            for dep_id in dep_list:
                if dep_id in task_map:
                    dep_name = escape(task_map[dep_id].title)
                    dep_chips.append(
                        f'<span class="dep-chip">'
                        f"&#8594; {dep_name}"
                        f'<button class="dep-remove" '
                        f'hx-post="/artifacts/{artifact_id}/flow/remove-dependency"'
                        f" hx-vals='{{\"source_id\": \"{nid}\","
                        f' "target_id": "{dep_id}"}}\''
                        f' hx-target="#flow-content" hx-swap="innerHTML"'
                        f' title="Remove">&times;</button>'
                        f"</span>"
                    )

            dep_html = " ".join(dep_chips) if dep_chips else ""

            rows.append(
                f'<tr data-node-id="{nid}">'
                f'<td class="task-name">'
                f'<span class="drag-handle"'
                f' title="Drag to link dependency">&#x1f517;</span>'
                f"{name}</td>"
                f'<td class="dep-arrows" data-deps="{dep_str}">'
                f"{dep_html}"
                f"</td></tr>"
            )

        return (
            '<table class="flow-deps">'
            "<thead><tr><th>Task</th><th>Dependencies</th></tr></thead>"
            f'<tbody>{"".join(rows)}</tbody>'
            "</table>"
        )

    def _render_dag(
        self, tasks: list[Task], artifact_id: NodeId,
        db: SecureGraphDB, session: Session,
    ) -> str:
        """Render DAG view — Gantt-style layout with nodes and dependency arrows."""
        if not tasks:
            return _empty_state(
                "Click <strong>+ Task</strong> above to add your first task."
            )

        sorted_tasks, preds = _topological_sort(tasks, db)

        # Compute layers: layer[nid] = longest path from a root
        layer: dict[NodeId, int] = {}
        for task in sorted_tasks:
            nid = task.meta.id
            parent_layers = [layer[p] for p in preds[nid] if p in layer]
            layer[nid] = (max(parent_layers) + 1) if parent_layers else 0

        max_layer = max(layer.values(), default=0)
        num_cols = max_layer + 1

        rows: list[str] = []
        for i, task in enumerate(sorted_tasks):
            name = escape(task.title)
            nid = task.meta.id
            deps_attr = " ".join(str(p) for p in preds[nid])
            col = layer[nid]
            # Position node as percentage within the cell
            pct = (col * 100 // num_cols) if num_cols > 1 else 0
            left_style = f"left:{pct}%" if num_cols > 1 else "left:0"

            left_td = f'<td class="dag-task-name">{name}</td>'
            right_td = (
                f'<td class="dag-cell">'
                f'<div class="dag-node" style="{left_style}"'
                f' data-node-id="{nid}"'
                f' data-row="{i}"'
                f' data-deps="{deps_attr}">'
                f'<span class="dag-label">{name}</span>'
                f'</div>'
                f'</td>'
            )
            rows.append(f"<tr>{left_td}{right_td}</tr>")

        return (
            f'<div class="dag-container">'
            f'<table class="flow-dag-table">'
            f"<thead><tr><th>Task</th><th>Dependencies</th></tr></thead>"
            f'<tbody>{"".join(rows)}</tbody>'
            f"</table>"
            f'<svg class="dag-edges"></svg>'
            f"</div>"
        )

    def _render_kanban(
        self, tasks: list[Task], artifact_id: NodeId | None = None,
    ) -> str:
        """Render Kanban view — columns by status."""
        columns: dict[str, list[Task]] = {s: [] for s in _STATUS_LABELS}
        for task in tasks:
            col = task.status if task.status in columns else "todo"
            columns[col].append(task)

        col_html: list[str] = []
        for status, label in _STATUS_LABELS.items():
            cards = columns[status]
            if not cards:
                card_html = '<div class="kanban-empty">No tasks</div>'
            else:
                card_parts = []
                for task in cards:
                    name = escape(task.title)
                    due = (
                        f'<span class="due-date">{task.due_date.strftime("%Y-%m-%d")}</span>'
                        if task.due_date else ""
                    )
                    nid = task.meta.id
                    toggle_btn = ""
                    if artifact_id is not None:
                        btn_label = _TOGGLE_LABELS.get(task.status, "&#x21bb;")
                        toggle_btn = (
                            f'<button class="btn btn-sm kanban-toggle"'
                            f' hx-post="/artifacts/{artifact_id}/flow/toggle-task"'
                            f" hx-vals='{{\"node_id\": \"{nid}\"}}'"
                            f' hx-target="#flow-content"'
                            f' hx-swap="innerHTML"'
                            f' title="Move to next status">'
                            f"{btn_label}</button>"
                        )
                    card_parts.append(
                        f'<div class="kanban-card" data-node-id="{nid}">'
                        f"{toggle_btn}"
                        f'<span class="card-title">{name}</span>{due}'
                        f"</div>"
                    )
                card_html = "".join(card_parts)

            add_form = ""
            if artifact_id is not None:
                add_form = (
                    f'<div class="kanban-add">'
                    f'<form hx-post="/artifacts/{artifact_id}/flow/create-task"'
                    f' hx-target="#flow-content" hx-swap="innerHTML"'
                    f' style="display:flex;gap:0.25rem;">'
                    f'<input type="hidden" name="mode" value="kanban" />'
                    f'<input type="text" name="title" placeholder="Add task..."'
                    f' class="kanban-add-input" />'
                    f'<button type="submit" class="btn btn-sm">+</button>'
                    f'</form></div>'
                )

            col_html.append(
                f'<div class="kanban-column" data-status="{status}">'
                f'<h3>{label}</h3>{card_html}{add_form}'
                f"</div>"
            )

        return f'<div class="flow-kanban">{"".join(col_html)}</div>'

    # ------------------------------------------------------------------
    # Apply actions
    # ------------------------------------------------------------------

    def apply_action(
        self,
        db: SecureGraphDB,
        session: Session,
        artifact_id: NodeId,
        action: LensAction,
    ) -> None:
        """Translate a FlowLens action into graph operations."""
        aid_str = str(artifact_id.value)
        principal_id = session.principal.id.value
        with db._db.action_group(principal_id, aid_str):
            self._apply_action_inner(db, session, artifact_id, action)

    def _apply_action_inner(
        self,
        db: SecureGraphDB,
        session: Session,
        artifact_id: NodeId,
        action: LensAction,
    ) -> None:
        """Inner dispatch for apply_action (already inside action_group)."""
        match action:
            case CreateTask(
                parent_id=parent_id, title=title, position=pos,
                due_date=due, start_date=start, end_date=end,
            ):
                self._create_task(
                    db, session, parent_id, title, pos, due, start, end,
                )
            case CreateTaskGroup(parent_id=parent_id, title=title, position=pos):
                self._create_task(db, session, parent_id, title, pos)
            case UpdateTask(task_id=task_id, title=title):
                self._update_task(db, session, task_id, title)
            case ToggleTask(task_id=task_id):
                self._toggle_task(db, session, task_id)
            case SetDependency(source_task_id=src, target_task_id=tgt):
                self._set_dependency(db, session, artifact_id, src, tgt)
            case RemoveDependency(source_task_id=src, target_task_id=tgt):
                self._remove_dependency(db, session, src, tgt)
            case SetTaskStatus(task_id=task_id, status=status):
                self._set_task_status(db, session, task_id, status)
            case SetDueDate(task_id=task_id, due_date=due):
                self._set_due_date(db, session, task_id, due)
            case SetDateRange(task_id=task_id, start_date=start, end_date=end):
                self._set_date_range(db, session, task_id, start, end)
            case ReorderNodes(parent_id=parent_id, new_order=new_order):
                self._reorder(db, session, parent_id, new_order)
            case DeleteNode(node_id=node_id):
                self._delete_task(db, session, node_id)
            case RenameArtifact(artifact_id=aid, title=title):
                self._rename_artifact(db, session, aid, title)
            case _:
                msg = f"FlowLens does not support action: {type(action).__name__}"
                raise ValueError(msg)

    # ------------------------------------------------------------------
    # Action implementations
    # ------------------------------------------------------------------

    def _create_task(
        self, db: SecureGraphDB, session: Session,
        parent_id: NodeId, title: str, position: int,
        due_date: object = None, start_date: object = None, end_date: object = None,
    ) -> NodeId:
        task = Task(
            meta=make_node_metadata(NodeType.TASK),
            title=title,
            due_date=due_date,  # type: ignore[arg-type]
            start_date=start_date,  # type: ignore[arg-type]
            end_date=end_date,  # type: ignore[arg-type]
        )
        task_id = db.create_node(session, task)
        edge = Edge(
            id=EdgeId.generate(),
            source=parent_id,
            target=task_id,
            edge_type=EdgeType.CONTAINS,
            created_at=utc_now(),
        )
        db.create_edge(session, edge)
        return task_id

    def _update_task(
        self, db: SecureGraphDB, session: Session, task_id: NodeId, title: str,
    ) -> None:
        task = db.get_node(session, task_id)
        if task is None or not isinstance(task, Task):
            msg = f"Task not found: {task_id}"
            raise ValueError(msg)
        updated = replace(task, title=title)
        db.update_node(session, updated)

    def _set_task_status(
        self, db: SecureGraphDB, session: Session, task_id: NodeId, status: str,
    ) -> None:
        if status not in VALID_STATUSES:
            msg = f"Invalid task status: {status!r}"
            raise ValueError(msg)
        task = db.get_node(session, task_id)
        if task is None or not isinstance(task, Task):
            msg = f"Task not found: {task_id}"
            raise ValueError(msg)
        completed = status == "done"
        updated = replace(task, status=status, completed=completed)
        db.update_node(session, updated)

    def _toggle_task(
        self, db: SecureGraphDB, session: Session, task_id: NodeId,
    ) -> None:
        task = db.get_node(session, task_id)
        if task is None or not isinstance(task, Task):
            msg = f"Task not found: {task_id}"
            raise ValueError(msg)
        new_status = _STATUS_CYCLE.get(task.status, "todo")
        completed = new_status == "done"
        updated = replace(task, status=new_status, completed=completed)
        db.update_node(session, updated)

    def _set_dependency(
        self, db: SecureGraphDB, session: Session,
        artifact_id: NodeId, source_id: NodeId, target_id: NodeId,
    ) -> None:
        # Check for circular dependency
        if _would_create_cycle(db, source_id, target_id):
            msg = f"Adding dependency {source_id} → {target_id} would create a cycle"
            raise ValueError(msg)

        edge = Edge(
            id=EdgeId.generate(),
            source=source_id,
            target=target_id,
            edge_type=EdgeType.DEPENDS_ON,
            created_at=utc_now(),
        )
        db.create_edge(session, edge)

    def _remove_dependency(
        self, db: SecureGraphDB, session: Session,
        source_id: NodeId, target_id: NodeId,
    ) -> None:
        edges = db._db.get_edges_from(source_id)
        for edge in edges:
            if edge.edge_type == EdgeType.DEPENDS_ON and edge.target == target_id:
                db.delete_edge(session, edge.id)
                return

    def _set_due_date(
        self, db: SecureGraphDB, session: Session,
        task_id: NodeId, due_date: object,
    ) -> None:
        task = db.get_node(session, task_id)
        if task is None or not isinstance(task, Task):
            msg = f"Task not found: {task_id}"
            raise ValueError(msg)
        updated = replace(task, due_date=due_date)
        db.update_node(session, updated)

    def _set_date_range(
        self, db: SecureGraphDB, session: Session,
        task_id: NodeId, start_date: object, end_date: object,
    ) -> None:
        task = db.get_node(session, task_id)
        if task is None or not isinstance(task, Task):
            msg = f"Task not found: {task_id}"
            raise ValueError(msg)
        updated = replace(task, start_date=start_date, end_date=end_date)
        db.update_node(session, updated)

    def _reorder(
        self, db: SecureGraphDB, session: Session,
        parent_id: NodeId, new_order: tuple[NodeId, ...],
    ) -> None:
        op = ReorderChildren(
            parent_id=parent_id,
            new_order=new_order,
            parent_ops=(),
            timestamp=utc_now(),
        )
        db._db.apply(op)

    def _rename_artifact(
        self, db: SecureGraphDB, session: Session,
        artifact_id: NodeId, title: str,
    ) -> None:
        art = db.get_node(session, artifact_id)
        if art is not None and isinstance(art, Artifact):
            updated = replace(art, title=title)
            db.update_node(session, updated)

    def _delete_task(
        self, db: SecureGraphDB, session: Session, node_id: NodeId,
    ) -> None:
        # Delete DEPENDS_ON edges involving this task
        edges = db._db.get_edges_from(node_id)
        for edge in edges:
            if edge.edge_type == EdgeType.DEPENDS_ON:
                db.delete_edge(session, edge.id)
        # Also delete edges targeting this task
        # (We need to check all tasks for edges pointing to this one)
        # For simplicity, delete the CONTAINS edge and the node
        db.delete_node(session, node_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _would_create_cycle(
    db: SecureGraphDB, source_id: NodeId, target_id: NodeId,
) -> bool:
    """Check if adding source → target dependency would create a cycle.

    A cycle exists if target can already reach source via DEPENDS_ON edges.
    """
    if source_id == target_id:
        return True

    visited: set[NodeId] = set()
    stack = [target_id]
    while stack:
        current = stack.pop()
        if current == source_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        edges = db._db.get_edges_from(current)
        for edge in edges:
            if edge.edge_type == EdgeType.DEPENDS_ON:
                stack.append(edge.target)
    return False


def _topological_sort(
    tasks: list[Task], db: SecureGraphDB,
) -> tuple[list[Task], dict[NodeId, set[NodeId]]]:
    """Sort tasks respecting DEPENDS_ON edges (topological order).

    Returns ``(sorted_tasks, predecessors)`` where *predecessors* maps each
    task id to the set of task ids it directly depends on.
    """
    task_map = {t.meta.id: t for t in tasks}
    task_ids = set(task_map.keys())

    # Build adjacency: task -> set of tasks it depends on (predecessors)
    predecessors: dict[NodeId, set[NodeId]] = {tid: set() for tid in task_ids}
    for tid in task_ids:
        edges = db._db.get_edges_from(tid)
        for edge in edges:
            if edge.edge_type == EdgeType.DEPENDS_ON and edge.target in task_ids:
                predecessors[tid].add(edge.target)

    # Save original predecessor map before Kahn's mutates it
    predecessors_orig: dict[NodeId, set[NodeId]] = {
        tid: set(preds) for tid, preds in predecessors.items()
    }

    # Kahn's algorithm
    result: list[Task] = []
    ready = [tid for tid, preds in predecessors.items() if not preds]

    while ready:
        tid = ready.pop(0)
        result.append(task_map[tid])
        for other_tid, preds in predecessors.items():
            if tid in preds:
                preds.discard(tid)
                if not preds:
                    ready.append(other_tid)

    # Add any remaining tasks (in case of cycles, which shouldn't happen)
    visited = {t.meta.id for t in result}
    for task in tasks:
        if task.meta.id not in visited:
            result.append(task)

    return result, predecessors_orig
