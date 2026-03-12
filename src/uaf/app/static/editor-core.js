var UAF = window.UAF || {};

UAF.debounce = function(fn, ms) {
    var timer;
    var lastCtx, lastArgs;
    var wrapper = function() {
        lastCtx = this;
        lastArgs = arguments;
        clearTimeout(timer);
        timer = setTimeout(function() { timer = null; fn.apply(lastCtx, lastArgs); lastArgs = null; }, ms);
    };
    wrapper.flush = function() {
        if (timer) { clearTimeout(timer); timer = null; fn.apply(lastCtx, lastArgs); lastArgs = null; }
    };
    return wrapper;
};

UAF.SlashMenu = {
    el: null,
    items: [
        {label: 'Paragraph', style: 'paragraph', icon: 'P'},
        {label: 'Heading 1', style: 'heading', level: 1, icon: 'H1'},
        {label: 'Heading 2', style: 'heading', level: 2, icon: 'H2'},
        {label: 'Heading 3', style: 'heading', level: 3, icon: 'H3'},
        {label: 'Code Block', style: 'code_block', icon: '<>'},
        {label: 'Bullet List', style: 'bulleted_list', icon: '\u2022'},
        {label: 'Numbered List', style: 'numbered_list', icon: '1.'},
        {label: 'Quote', style: 'quote', icon: '\u201C'},
        {label: 'Divider', style: 'divider', icon: '\u2014'}
    ],
    activeIndex: 0,
    visible: false,
    callback: null,

    init: function() {
        this.el = document.getElementById('slash-menu');
        if (!this.el) return;
    },

    show: function(x, y, cb) {
        if (!this.el) this.init();
        if (!this.el) return;
        this.callback = cb;
        this.activeIndex = 0;
        this.filter('');
        this.el.style.left = x + 'px';
        this.el.style.top = y + 'px';
        this.el.style.display = 'block';
        this.visible = true;
    },

    hide: function() {
        if (this.el) this.el.style.display = 'none';
        this.visible = false;
        this.callback = null;
    },

    filter: function(query) {
        var q = query.toLowerCase();
        var html = '';
        var filtered = this.items.filter(function(item) {
            return !q || item.label.toLowerCase().indexOf(q) >= 0;
        });
        for (var i = 0; i < filtered.length; i++) {
            var cls = i === this.activeIndex ? 'slash-menu-item active' : 'slash-menu-item';
            html += '<div class="' + cls + '" data-index="' + i + '">' +
                '<span class="slash-icon">' + filtered[i].icon + '</span>' +
                '<span>' + filtered[i].label + '</span></div>';
        }
        if (this.el) this.el.innerHTML = html;
        this._filtered = filtered;
    },

    navigate: function(dir) {
        if (!this._filtered) return;
        this.activeIndex = Math.max(0, Math.min(this._filtered.length - 1, this.activeIndex + dir));
        this.filter(this._currentQuery || '');
    },

    select: function() {
        if (!this._filtered || !this._filtered[this.activeIndex]) return;
        var item = this._filtered[this.activeIndex];
        if (this.callback) this.callback(item);
        this.hide();
    },

    _currentQuery: '',
    _filtered: []
};

UAF.FloatingToolbar = {
    el: null,
    init: function() {
        this.el = document.getElementById('format-toolbar');
    },
    show: function(x, y) {
        if (!this.el) this.init();
        if (!this.el) return;
        this.el.style.left = x + 'px';
        this.el.style.top = (y - 40) + 'px';
        this.el.style.display = 'flex';
    },
    hide: function() {
        if (this.el) this.el.style.display = 'none';
    }
};

UAF.ContextMenu = {
    el: null,
    init: function() {
        this.el = document.createElement('div');
        this.el.className = 'context-menu';
        this.el.style.display = 'none';
        document.body.appendChild(this.el);
    },
    show: function(x, y, items) {
        if (!this.el) this.init();
        var html = '';
        for (var i = 0; i < items.length; i++) {
            if (items[i].separator) {
                html += '<div class="context-menu-sep"></div>';
            } else {
                html += '<div class="context-menu-item" data-action="' + items[i].action + '">' + items[i].label + '</div>';
            }
        }
        this.el.innerHTML = html;
        this.el.style.left = x + 'px';
        this.el.style.top = y + 'px';
        this.el.style.display = 'block';
        var self = this;
        setTimeout(function() {
            document.addEventListener('click', function handler() {
                self.hide();
                document.removeEventListener('click', handler);
            });
        }, 0);
    },
    hide: function() {
        if (this.el) this.el.style.display = 'none';
    },
    onAction: null
};

UAF.saveCE = function(url, data) {
    return fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams(data)
    });
};

/** Synchronous save — blocks until complete. Use only for flush-before-navigate. */
UAF.saveCESync = function(url, data) {
    var xhr = new XMLHttpRequest();
    xhr.open('POST', url, false);  // synchronous
    xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
    xhr.send(new URLSearchParams(data).toString());
};

UAF.initUndoShortcuts = function(artifactId, targetId) {
    document.addEventListener('keydown', function(e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
            e.preventDefault();
            htmx.ajax('POST', '/artifacts/' + artifactId + '/undo', {target: '#' + targetId, swap: 'innerHTML'});
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 'z' && e.shiftKey) {
            e.preventDefault();
            htmx.ajax('POST', '/artifacts/' + artifactId + '/redo', {target: '#' + targetId, swap: 'innerHTML'});
        }
    });
};

window.UAF = UAF;
