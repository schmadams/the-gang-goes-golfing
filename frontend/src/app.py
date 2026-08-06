import logging
import os

import dash
import dash_bootstrap_components as dbc
from dash import html

# Quiet the default Flask dev-server request logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = dash.Dash(
    __name__,
    use_pages=True,
    suppress_callback_exceptions=True,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
)

server = app.server  
server.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

app.layout = html.Div(
    dash.page_container,  # renders whichever page matches the current URL
    style={"background": "#261C67", "minHeight": "100vh", "width": "100%"},
)

if __name__ == "__main__":
    app.run(debug=False)  # app.run_server is deprecated in newer Dash versions