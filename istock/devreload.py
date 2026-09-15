"""Development live-reload.

Only active when the app runs in debug mode (``flask run`` with the bundled
``.flaskenv``). Two layers:

* Werkzeug's reloader restarts the server when a Python file changes, and
  Jinja re-reads templates on every request (TEMPLATES_AUTO_RELOAD).
* The browser polls ``/__livereload`` once a second. The response carries a
  token made of the server's boot id and the newest template/static mtime;
  when the token changes (file edited or server restarted) the page reloads.

Polling instead of a long-lived EventSource matters: browsers allow only ~6
HTTP/1.1 connections per host, and one stream per open tab quickly starves
real requests (form posts, fetches), making the app look randomly slow.
"""
import logging
import os
import time

from flask import jsonify

WATCH_DIRS = ("templates", "static")
WATCH_EXT = (".html", ".css", ".js", ".svg")


def _snapshot(root):
    latest = 0.0
    for sub in WATCH_DIRS:
        for dirpath, _dirs, files in os.walk(os.path.join(root, sub)):
            for name in files:
                if name.endswith(WATCH_EXT):
                    try:
                        latest = max(latest, os.stat(os.path.join(dirpath, name)).st_mtime)
                    except OSError:
                        pass
    return latest


def init_app(app):
    if not app.debug or app.testing:
        return
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # never cache static files in dev
    root = app.root_path

    boot_id = str(time.time())
    # keep the once-a-second polls out of the request log
    logging.getLogger("werkzeug").addFilter(
        lambda record: "/__livereload" not in record.getMessage())

    @app.route("/__livereload")
    def livereload():
        resp = jsonify(token="%s:%s" % (boot_id, _snapshot(root)))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def _flag():
        return {"livereload": True}
