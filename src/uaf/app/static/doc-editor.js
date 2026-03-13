(function() {
    var artifactId = document.querySelector('[data-artifact-id]');
    if (!artifactId) {
        // Try to get from URL
        var m = window.location.pathname.match(/\/artifacts\/([^/]+)/);
        if (m) artifactId = m[1];
        else return;
    } else {
        artifactId = artifactId.dataset.artifactId || artifactId.getAttribute('data-artifact-id');
    }

    var content = document.getElementById('doc-content');
    if (!content) return;

    // --- Contenteditable auto-save ---
    var _pendingEl = null;

    function _buildSaveData(el) {
        var nodeId = el.dataset.nodeId;
        var text = el.innerText;
        var fmt = 'plain';
        if (el.innerHTML !== el.innerText && el.innerHTML.match(/<[biu]>|<code>|<a /)) {
            text = el.innerHTML;
            fmt = 'html';
        }
        return {node_id: nodeId, text: text, content_format: fmt};
    }

    var debouncedSave = UAF.debounce(function(el) {
        _pendingEl = null;
        UAF.saveCE('/artifacts/' + artifactId + '/action/update-text', _buildSaveData(el));
    }, 500);

    /** Flush any pending contenteditable save synchronously (blocks until done). */
    function flushSave() {
        if (_pendingEl) {
            debouncedSave.flush();  // clears the timer
            // The flush called the debounced fn which uses async fetch —
            // but we already cleared _pendingEl. Do a sync save instead.
        }
    }

    // Override: track the dirty element and use sync save on flush
    var _origFlush = debouncedSave.flush;
    debouncedSave.flush = function() {
        if (_pendingEl) {
            var data = _buildSaveData(_pendingEl);
            _pendingEl = null;
            // Cancel the pending async timer
            _origFlush();
            // Do a synchronous XHR so the data is persisted before the next request
            UAF.saveCESync('/artifacts/' + artifactId + '/action/update-text', data);
        }
    };

    content.addEventListener('input', function(e) {
        var body = e.target.closest('.block-body');
        if (body && body.contentEditable === 'true') {
            _pendingEl = body;
            debouncedSave(body);
        }
    });

    // --- Flush before any HTMX request that would swap doc-content ---
    document.body.addEventListener('htmx:before-request', function(e) {
        var target = e.detail.target || (e.detail.elt && e.detail.elt.getAttribute('hx-target'));
        // Flush if any HTMX request targets doc-content (view toggles, inserts, etc.)
        if (_pendingEl) {
            debouncedSave.flush();
        }
    });

    // --- SortableJS ---
    function initSortable() {
        if (typeof Sortable === 'undefined') return;
        var el = document.getElementById('doc-content');
        if (!el || el._sortable) return;
        el._sortable = Sortable.create(el, {
            handle: '.block-handle',
            ghostClass: 'block-ghost',
            animation: 150,
            onEnd: function() {
                var blocks = el.querySelectorAll('.doc-block[data-node-id]');
                var ids = [];
                blocks.forEach(function(b) { ids.push(b.dataset.nodeId); });
                UAF.saveCE('/artifacts/' + artifactId + '/action/reorder', {
                    order: ids.join(',')
                });
            }
        });
    }
    initSortable();

    // --- Slash menu ---
    content.addEventListener('keydown', function(e) {
        if (UAF.SlashMenu.visible) {
            if (e.key === 'ArrowDown') { e.preventDefault(); UAF.SlashMenu.navigate(1); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); UAF.SlashMenu.navigate(-1); }
            else if (e.key === 'Enter') { e.preventDefault(); UAF.SlashMenu.select(); }
            else if (e.key === 'Escape') { e.preventDefault(); UAF.SlashMenu.hide(); }
            return;
        }

        var body = e.target.closest('.block-body');
        if (!body) return;

        if (e.key === '/' && body.innerText.trim() === '') {
            e.preventDefault();
            var rect = body.getBoundingClientRect();
            UAF.SlashMenu.show(rect.left, rect.bottom, function(item) {
                var nodeId = body.dataset.nodeId;
                UAF.saveCE('/artifacts/' + artifactId + '/action/convert', {
                    node_id: nodeId,
                    new_style: item.style,
                    level: item.level || 1
                }).then(function(resp) {
                    if (resp.ok) return resp.text();
                }).then(function(html) {
                    if (html) {
                        content.innerHTML = html;
                        if (typeof htmx !== 'undefined') htmx.process(content);
                        initSortable();
                    }
                });
            });
        }

        // Enter: split block
        if (e.key === 'Enter' && !e.shiftKey && body.dataset.type !== 'code_block') {
            e.preventDefault();
            var sel = window.getSelection();
            if (!sel || !sel.rangeCount) return;
            var range = sel.getRangeAt(0);
            var preRange = document.createRange();
            preRange.setStart(body, 0);
            preRange.setEnd(range.startContainer, range.startOffset);
            var preDiv = document.createElement('div');
            preDiv.appendChild(preRange.cloneContents());
            var postRange = document.createRange();
            postRange.setStart(range.endContainer, range.endOffset);
            postRange.setEndAfter(body.lastChild || body);
            var postDiv = document.createElement('div');
            postDiv.appendChild(postRange.cloneContents());
            var data = {
                node_id: body.dataset.nodeId,
                before_text: preDiv.innerText || '',
                after_text: postDiv.innerText || ''
            };
            fetch('/artifacts/' + artifactId + '/action/split-block', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: new URLSearchParams(data)
            }).then(function(r) { return r.text(); }).then(function(html) {
                content.innerHTML = html;
                if (typeof htmx !== 'undefined') htmx.process(content);
                initSortable();
                // Focus the new block
                var blocks = content.querySelectorAll('.block-body');
                var allBlocks = content.querySelectorAll('.doc-block');
                var idx = -1;
                for (var bi = 0; bi < allBlocks.length; bi++) {
                    if (allBlocks[bi].dataset.nodeId === body.dataset.nodeId) {
                        idx = bi;
                        break;
                    }
                }
                if (idx >= 0 && blocks[idx + 1]) {
                    blocks[idx + 1].focus();
                }
            });
        }

        // Backspace on empty block: delete
        if (e.key === 'Backspace' && body.innerText.trim() === '') {
            e.preventDefault();
            var prev = body.closest('.doc-block').previousElementSibling;
            var prevBody = prev ? prev.querySelector('.block-body') : null;
            fetch('/artifacts/' + artifactId + '/action/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: new URLSearchParams({node_id: body.dataset.nodeId})
            }).then(function(r) { return r.text(); }).then(function(html) {
                content.innerHTML = html;
                if (typeof htmx !== 'undefined') htmx.process(content);
                initSortable();
                if (prevBody) {
                    var prevId = prevBody.dataset.nodeId;
                    var newPrev = content.querySelector('[data-node-id="' + prevId + '"] .block-body');
                    if (newPrev) newPrev.focus();
                }
            });
        }

        // Ctrl+B, Ctrl+I
        if ((e.ctrlKey || e.metaKey) && e.key === 'b') {
            e.preventDefault();
            document.execCommand('bold');
            debouncedSave(body);
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 'i') {
            e.preventDefault();
            document.execCommand('italic');
            debouncedSave(body);
        }
    });

    // Slash menu filter on typing
    content.addEventListener('input', function(e) {
        if (UAF.SlashMenu.visible) {
            var body = e.target.closest('.block-body');
            if (body) {
                var text = body.innerText;
                if (text.startsWith('/')) {
                    UAF.SlashMenu._currentQuery = text.substring(1);
                    UAF.SlashMenu.filter(UAF.SlashMenu._currentQuery);
                } else {
                    UAF.SlashMenu.hide();
                }
            }
        }
    });

    // Slash menu click selection
    document.addEventListener('click', function(e) {
        var item = e.target.closest('.slash-menu-item');
        if (item) {
            var idx = parseInt(item.dataset.index, 10);
            UAF.SlashMenu.activeIndex = idx;
            UAF.SlashMenu.select();
        } else if (!e.target.closest('.slash-menu') && !e.target.closest('.block-body')) {
            UAF.SlashMenu.hide();
        }
    });

    // --- Floating toolbar on text selection ---
    document.addEventListener('selectionchange', function() {
        var sel = window.getSelection();
        if (!sel || sel.isCollapsed || !sel.rangeCount) {
            UAF.FloatingToolbar.hide();
            return;
        }
        var anchor = sel.anchorNode;
        if (!anchor) { UAF.FloatingToolbar.hide(); return; }
        var el = anchor.nodeType === 3 ? anchor.parentElement : anchor;
        if (!el || !el.closest || !el.closest('.block-body')) {
            UAF.FloatingToolbar.hide();
            return;
        }
        var range = sel.getRangeAt(0);
        var rect = range.getBoundingClientRect();
        UAF.FloatingToolbar.show(rect.left + rect.width / 2 - 50, rect.top);
    });

    // Toolbar button actions
    document.addEventListener('click', function(e) {
        var btn = e.target.closest('.format-toolbar button');
        if (!btn) return;
        e.preventDefault();
        var cmd = btn.dataset.cmd;
        if (cmd === 'bold') document.execCommand('bold');
        else if (cmd === 'italic') document.execCommand('italic');
        else if (cmd === 'code') {
            var sel = window.getSelection();
            if (sel && sel.rangeCount) {
                var range = sel.getRangeAt(0);
                var code = document.createElement('code');
                try { range.surroundContents(code); } catch(_) { /* ignore */ }
            }
        }
        // Trigger save for the active block
        var sel2 = window.getSelection();
        if (sel2 && sel2.anchorNode) {
            var el2 = sel2.anchorNode.nodeType === 3 ? sel2.anchorNode.parentElement : sel2.anchorNode;
            var bodyEl = el2 ? el2.closest('.block-body') : null;
            if (bodyEl) debouncedSave(bodyEl);
        }
    });

    // --- Re-init after HTMX swaps ---
    content.addEventListener('htmx:after-swap', function() {
        if (content._sortable) {
            content._sortable.destroy();
            content._sortable = null;
        }
        initSortable();
    });
})();
