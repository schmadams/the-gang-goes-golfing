# target path: frontend/src/layouts/panel_navbar.py (new file)
from dash import html


def build_panel_navbar(title, action=None):
    """A small header bar for the top of a panel: title on the left,
    an optional action (button/link component) on the right."""
    children = [html.H3(title, className="t3g-panel-navbar-title")]

    if action is not None:
        children.append(html.Div(action, className="t3g-panel-navbar-action"))

    return html.Div(className="t3g-panel-navbar", children=children)