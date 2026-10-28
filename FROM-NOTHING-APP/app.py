"""WSGI entrypoint: ``app = create_app()`` plus a guarded serve command (S02).

* Posit Connect: ``rsconnect deploy api -e app:app .``
* VMware Tanzu: the ``Procfile`` runs ``python app.py`` (Waitress on ``$PORT``).
* Local: ``python app.py`` (127.0.0.1:5055 by default).

Importing this module never opens a database, acquires sources, writes
files, runs a solver or kills a port occupant.
"""
from focus.web.app import create_app

app = create_app()

if __name__ == "__main__":
    from focus.cli import main

    raise SystemExit(main(["serve"]))
