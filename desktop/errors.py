# SPDX-License-Identifier: Apache-2.0
"""Error pages the desktop window shows when the controller cannot be used."""
import html

import i18n
from i18n import tr


def error_page(kind, detail):
    """kind: foreign_server | controller_failed. `detail` is shown escaped."""
    return (f'<!doctype html><html lang="{i18n.get_lang()}"><head><meta charset="utf-8"><style>'
            "body{font:15px system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1.5rem;color:#1c1c1e}"
            "h1{font-size:1.4rem}code{background:#eee;padding:.1rem .3rem;border-radius:4px}</style></head><body>"
            f"<h1>{html.escape(tr(f'app.error.{kind}.title'))}</h1>"
            f"<p>{html.escape(tr(f'app.error.{kind}.text'))}</p>"
            f"<p><code>{html.escape(str(detail))}</code></p></body></html>")
