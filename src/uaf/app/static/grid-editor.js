(function() {
    var content = document.getElementById('grid-content');
    if (!content) return;

    var artifactId = content.dataset.artifactId;
    var selectedCell = null;
    var editingCell = null;

    // --- Cell selection ---
    content.addEventListener('click', function(e) {
        var td = e.target.closest('td[data-row][data-col]');
        if (!td) return;
        if (editingCell && editingCell !== td) commitEdit();
        selectCell(td);
    });

    // --- Double-click to edit ---
    content.addEventListener('dblclick', function(e) {
        var td = e.target.closest('td[data-row][data-col]');
        if (!td) return;
        startEdit(td);
    });

    // --- Start editing on typing ---
    document.addEventListener('keydown', function(e) {
        if (!selectedCell || editingCell) return;
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
            startEdit(selectedCell, '');
        }
    });

    // --- Keyboard nav ---
    content.addEventListener('keydown', function(e) {
        if (editingCell) {
            if (e.key === 'Enter') {
                e.preventDefault();
                commitEdit();
                moveSelection(1, 0);
            } else if (e.key === 'Tab') {
                e.preventDefault();
                commitEdit();
                moveSelection(0, e.shiftKey ? -1 : 1);
            } else if (e.key === 'Escape') {
                e.preventDefault();
                cancelEdit();
            }
            return;
        }
        if (!selectedCell) return;
        if (e.key === 'ArrowDown') { e.preventDefault(); moveSelection(1, 0); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); moveSelection(-1, 0); }
        else if (e.key === 'ArrowRight') { e.preventDefault(); moveSelection(0, 1); }
        else if (e.key === 'ArrowLeft') { e.preventDefault(); moveSelection(0, -1); }
        else if (e.key === 'Enter') { e.preventDefault(); startEdit(selectedCell); }
        else if (e.key === 'Tab') { e.preventDefault(); moveSelection(0, e.shiftKey ? -1 : 1); }
        else if (e.key === 'Delete' || e.key === 'Backspace') {
            e.preventDefault();
            saveCell(selectedCell, '');
        }
    });

    // --- Context menu ---
    content.addEventListener('contextmenu', function(e) {
        var td = e.target.closest('td[data-row][data-col]');
        if (!td) return;
        e.preventDefault();
        selectCell(td);

        UAF.ContextMenu.show(e.clientX, e.clientY, [
            {label: 'Insert row above', action: 'insert-row-above'},
            {label: 'Insert row below', action: 'insert-row-below'},
            {label: 'Delete row', action: 'delete-row'},
            {separator: true},
            {label: 'Insert column left', action: 'insert-col-left'},
            {label: 'Insert column right', action: 'insert-col-right'},
            {label: 'Delete column', action: 'delete-col'}
        ]);
        var row = parseInt(td.dataset.row);
        var col = parseInt(td.dataset.col);
        UAF.ContextMenu.onAction = function(action) {
            var url = '/artifacts/' + artifactId + '/grid/';
            var data = {};
            switch (action) {
                case 'insert-row-above':
                    url += 'add-row'; data = {position: row}; break;
                case 'insert-row-below':
                    url += 'add-row'; data = {position: row + 1}; break;
                case 'delete-row':
                    url += 'delete-row'; data = {position: row}; break;
                case 'insert-col-left':
                    url += 'add-col'; data = {position: col}; break;
                case 'insert-col-right':
                    url += 'add-col'; data = {position: col + 1}; break;
                case 'delete-col':
                    url += 'delete-col'; data = {position: col}; break;
            }
            fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: new URLSearchParams(data)
            }).then(function(r) { return r.text(); }).then(function(html) {
                content.innerHTML = html;
                if (typeof htmx !== 'undefined') htmx.process(content);
            });
        };
    });

    // Context menu item click
    document.addEventListener('click', function(e) {
        var item = e.target.closest('.context-menu-item');
        if (item && UAF.ContextMenu.onAction) {
            UAF.ContextMenu.onAction(item.dataset.action);
        }
    });

    function selectCell(td) {
        if (selectedCell) selectedCell.classList.remove('cell-selected');
        selectedCell = td;
        td.classList.add('cell-selected');
        td.focus();
    }

    function startEdit(td, initialValue) {
        if (editingCell) commitEdit();
        editingCell = td;
        var current = initialValue !== undefined ? initialValue : td.textContent.trim();
        td.classList.add('cell-editing');
        td.innerHTML = '<input type="text" class="cell-input" value="' +
            current.replace(/"/g, '&quot;') + '" />';
        var input = td.querySelector('input');
        input.focus();
        if (initialValue === undefined) input.select();
    }

    function commitEdit() {
        if (!editingCell) return;
        var input = editingCell.querySelector('input');
        if (!input) { editingCell = null; return; }
        var value = input.value;
        editingCell.classList.remove('cell-editing');
        editingCell.textContent = value;
        saveCell(editingCell, value);
        editingCell = null;
    }

    function cancelEdit() {
        if (!editingCell) return;
        var input = editingCell.querySelector('input');
        editingCell.classList.remove('cell-editing');
        if (input) editingCell.textContent = input.defaultValue;
        editingCell = null;
    }

    function saveCell(td, value) {
        var nodeId = td.dataset.nodeId;

        if (nodeId) {
            UAF.saveCE('/artifacts/' + artifactId + '/grid/set-cell', {
                cell_id: nodeId,
                value: value
            });
        } else {
            UAF.saveCE('/artifacts/' + artifactId + '/grid/create-cell', {
                row: td.dataset.row,
                col: td.dataset.col,
                value: value
            }).then(function(r) { return r.text(); }).then(function(html) {
                content.innerHTML = html;
                if (typeof htmx !== 'undefined') htmx.process(content);
            });
        }
        td.textContent = value;
    }

    function moveSelection(dr, dc) {
        if (!selectedCell) return;
        var row = parseInt(selectedCell.dataset.row) + dr;
        var col = parseInt(selectedCell.dataset.col) + dc;
        var next = content.querySelector('td[data-row="' + row + '"][data-col="' + col + '"]');
        if (next) selectCell(next);
    }

    // Re-init after HTMX swaps
    content.addEventListener('htmx:after-swap', function() {
        selectedCell = null;
        editingCell = null;
    });
})();
