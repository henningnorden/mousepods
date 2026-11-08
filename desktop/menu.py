# SPDX-License-Identifier: Apache-2.0
"""Tray menu model (com.canonical.dbusmenu layout as plain Python data)."""
from i18n import tr

# (id, translation key or None for a separator, action)
ITEMS = (
    (1, "tray.open", "open"),
    (2, "tray.recenter", "recenter"),
    (3, "tray.toggle", "toggle"),
    (4, None, None),
    (5, "tray.quit", "quit"),
)


def layout(items=ITEMS, revision=1):
    """(revision, (0, {"children-display": "submenu"}, [(id, props, []), ...])) — the
    GetLayout reply before wrapping into GLib variants."""
    children = []
    for item_id, key, _action in items:
        props = {"type": "separator"} if key is None else {"label": tr(key), "enabled": True, "visible": True}
        children.append((item_id, props, []))
    return revision, (0, {"children-display": "submenu"}, children)


def action_for(item_id, items=ITEMS):
    return next((action for i, _k, action in items if i == item_id), None)
