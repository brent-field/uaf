/* Layout Inspector — interactive typographic debugging UI for layout view. */

(function () {
  "use strict";

  var selectedBlock = null;
  var tooltipTimer = null;

  /* ------------------------------------------------------------------ */
  /* Helpers                                                             */
  /* ------------------------------------------------------------------ */

  function getBlockProperties(el) {
    var s = el.style;
    return {
      nodeId: el.dataset.nodeId || "",
      nodeType: el.dataset.nodeType || "unknown",
      page: el.dataset.page,
      x: s.left,
      y: s.top,
      width: s.width,
      height: el.dataset.height,
      readingOrder: el.dataset.readingOrder,
      fontFamily: s.fontFamily,
      fontSize: s.fontSize,
      fontWeight: s.fontWeight || "normal",
      fontStyle: s.fontStyle || "normal",
      color: s.color || "#000000",
      rotation: el.dataset.rotation,
      firstLineWeight: el.dataset.firstLineWeight,
      text: el.textContent || "",
    };
  }

  function formatRow(label, value) {
    if (value === undefined || value === null || value === "") return "";
    return (
      '<div class="inspector-row">' +
      '<span class="inspector-label">' + label + '</span>' +
      '<span class="inspector-value">' + escapeHtml(String(value)) + '</span>' +
      "</div>"
    );
  }

  function formatSection(title) {
    return '<div class="inspector-section">' + escapeHtml(title) + "</div>";
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  function truncateText(text, max) {
    if (text.length <= max) return text.replace(/\n/g, "\u21b5");
    return text.slice(0, max).replace(/\n/g, "\u21b5") + "\u2026";
  }

  /* ------------------------------------------------------------------ */
  /* Tooltip                                                            */
  /* ------------------------------------------------------------------ */

  function showTooltip(block, evt) {
    var tip = document.getElementById("layout-tooltip");
    if (!tip) return;

    var props = getBlockProperties(block);
    var parts = [];
    if (props.fontFamily) parts.push(props.fontFamily);
    if (props.fontSize) parts.push(props.fontSize);
    if (props.fontWeight !== "normal") parts.push(props.fontWeight);
    tip.textContent = parts.join(" | ") || props.nodeType;
    tip.classList.remove("hidden");

    positionTooltip(tip, evt);
  }

  function positionTooltip(tip, evt) {
    var root = document.getElementById("doc-content");
    if (!root) return;
    var rect = root.getBoundingClientRect();
    var x = evt.clientX - rect.left + 12;
    var y = evt.clientY - rect.top + 12;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }

  function hideTooltip() {
    var tip = document.getElementById("layout-tooltip");
    if (tip) tip.classList.add("hidden");
  }

  /* ------------------------------------------------------------------ */
  /* Inspector panel                                                    */
  /* ------------------------------------------------------------------ */

  function showInspector(block) {
    /* Deselect previous */
    if (selectedBlock) selectedBlock.classList.remove("selected");

    /* Toggle off if clicking the same block */
    if (selectedBlock === block) {
      selectedBlock = null;
      hideInspector();
      return;
    }

    selectedBlock = block;
    block.classList.add("selected");

    var panel = document.getElementById("layout-inspector");
    var body = panel ? panel.querySelector(".inspector-body") : null;
    if (!panel || !body) return;

    var p = getBlockProperties(block);
    var lineCount = (p.text.match(/\n/g) || []).length + 1;

    var html = "";

    html += formatSection("Position & Size");
    html += formatRow("Page", p.page);
    html += formatRow("Position", p.x + ", " + p.y);
    html += formatRow("Width", p.width);
    html += formatRow("Height", p.height ? p.height + "pt" : "");
    html += formatRow("Reading order", p.readingOrder);

    html += formatSection("Typography");
    html += formatRow("Font family", p.fontFamily);
    html += formatRow("Size", p.fontSize);
    html += formatRow("Weight", p.fontWeight);
    html += formatRow("Style", p.fontStyle);
    html += formatRow("Colour", p.color);
    html += formatRow("1st line weight", p.firstLineWeight);

    if (p.rotation) {
      html += formatSection("Rotation");
      html += formatRow("Angle", p.rotation + "\u00b0");
      html += formatRow("Origin", "top left");
    }

    html += formatSection("Content");
    html += formatRow("Preview", truncateText(p.text, 200));
    html += formatRow("Lines", String(lineCount));
    html += formatRow("Characters", String(p.text.length));

    html += formatSection("Identity");
    html += formatRow("Node ID", p.nodeId);
    html += formatRow("Type", p.nodeType);

    body.innerHTML = html;
    panel.classList.remove("hidden");
  }

  /* Exported to global scope for the onclick handler in the template. */
  window.hideInspector = function hideInspector() {
    if (selectedBlock) {
      selectedBlock.classList.remove("selected");
      selectedBlock = null;
    }
    var panel = document.getElementById("layout-inspector");
    if (panel) panel.classList.add("hidden");
  };

  /* ------------------------------------------------------------------ */
  /* Event delegation on #doc-content (survives HTMX swaps)             */
  /* ------------------------------------------------------------------ */

  document.addEventListener("DOMContentLoaded", function () {
    var root = document.getElementById("doc-content");
    if (!root) return;

    root.addEventListener("mouseover", function (e) {
      var block = e.target.closest(".layout-block");
      if (block) showTooltip(block, e);
    });

    root.addEventListener("mousemove", function (e) {
      var tip = document.getElementById("layout-tooltip");
      if (tip && !tip.classList.contains("hidden")) {
        positionTooltip(tip, e);
      }
    });

    root.addEventListener("mouseout", function (e) {
      var block = e.target.closest(".layout-block");
      if (block) hideTooltip();
    });

    root.addEventListener("click", function (e) {
      var block = e.target.closest(".layout-block");
      if (block) {
        e.stopPropagation();
        showInspector(block);
      }
    });

    /* Keyboard shortcuts */
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        window.hideInspector();
      }
      if (
        e.key === "c" &&
        !e.ctrlKey &&
        !e.metaKey &&
        selectedBlock &&
        document.getElementById("layout-inspector") &&
        !document
          .getElementById("layout-inspector")
          .classList.contains("hidden")
      ) {
        var nid = selectedBlock.dataset.nodeId;
        if (nid && navigator.clipboard) {
          navigator.clipboard.writeText(nid);
        }
      }
    });
  });
})();
