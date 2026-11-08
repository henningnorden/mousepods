# SPDX-License-Identifier: Apache-2.0
"""Tray icon via StatusNotifierItem + com.canonical.dbusmenu over Gio (KDE Plasma, and GNOME with
the AppIndicator extension). No libappindicator needed, so it works from the GNOME Flatpak runtime."""
import logging
import os

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from desktop import menu  # noqa: E402
from i18n import tr  # noqa: E402

APP_ID = "io.github.henningnorden.MousePods"
log = logging.getLogger(__name__)

SNI_XML = """<node><interface name="org.kde.StatusNotifierItem">
 <property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
 <property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
 <property name="IconName" type="s" access="read"/><property name="ItemIsMenu" type="b" access="read"/>
 <property name="Menu" type="o" access="read"/>
 <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
 <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
 <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
 <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
 <signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewStatus"><arg type="s"/></signal>
</interface></node>"""

MENU_XML = """<node><interface name="com.canonical.dbusmenu">
 <property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
 <property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
 <method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
  <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
 <method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/>
  <arg type="a(ia{sv})" direction="out"/></method>
 <method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
 <method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/>
  <arg type="u" direction="in"/></method>
 <method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
 <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
 <method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/>
  <arg type="ai" direction="out"/></method>
 <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
</interface></node>"""


def _props(d):
    return {k: GLib.Variant("s" if isinstance(v, str) else "b", v) for k, v in d.items()}


def _layout_variant(node):
    item_id, props, children = node
    return GLib.Variant("(ia{sv}av)", (item_id, _props(props), [_layout_variant(c) for c in children]))


class Tray:
    def __init__(self, on_action):
        self.on_action = on_action
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        dbm = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self.bus.register_object("/StatusNotifierItem", sni, self._sni_call, self._sni_get, None)
        self.bus.register_object("/MenuBar", dbm, self._menu_call, self._menu_get, None)
        try:
            self.bus.call_sync("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                               "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                               GLib.Variant("(s)", (self.bus.get_unique_name(),)), None,
                               Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error as e:
            log.warning("no tray available (StatusNotifierWatcher): %s", e.message)

    # ---- StatusNotifierItem
    def _sni_get(self, _conn, _sender, _path, _iface, name):
        icon = APP_ID if os.environ.get("FLATPAK_ID") else "input-mouse"
        values = {"Category": ("s", "ApplicationStatus"), "Id": ("s", APP_ID), "Title": ("s", tr("tray.title")),
                  "Status": ("s", "Active"), "IconName": ("s", icon), "ItemIsMenu": ("b", False),
                  "Menu": ("o", "/MenuBar")}
        sig, val = values[name]
        return GLib.Variant(sig, val)

    def _sni_call(self, _conn, _sender, _path, _iface, method, _params, invocation):
        if method == "Activate":
            self.on_action("open")
        invocation.return_value(None)

    # ---- dbusmenu
    def _menu_get(self, _conn, _sender, _path, _iface, name):
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"), "IconThemePath": GLib.Variant("as", [])}[name]

    def _menu_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "GetLayout":
            rev, node = menu.layout()
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("u", rev), _layout_variant(node)))
        elif method == "GetGroupProperties":
            ids = params.unpack()[0]
            _rev, (_i, _p, children) = menu.layout()
            items = [(c[0], _props(c[1])) for c in children if not ids or c[0] in ids]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (items,)))
        elif method == "GetProperty":
            item_id, prop = params.unpack()
            _rev, (_i, _p, children) = menu.layout()
            props = next((c[1] for c in children if c[0] == item_id), {})
            invocation.return_value(GLib.Variant("(v)", (_props(props).get(prop, GLib.Variant("s", "")),)))
        elif method == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            if event_id == "clicked":
                action = menu.action_for(item_id)
                if action:
                    GLib.idle_add(self.on_action, action)
            invocation.return_value(None)
        elif method == "EventGroup":
            for item_id, event_id, _data, _ts in params.unpack()[0]:
                if event_id == "clicked" and menu.action_for(item_id):
                    GLib.idle_add(self.on_action, menu.action_for(item_id))
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)
