"""Point d'entrée du conteneur : ``python3 -m lody_auth``.

Tout se règle par variable d'environnement (même convention que Lody : ``LODY_AUTH_*``). Aucune valeur par
défaut pour ``LODY_AUTH_ADMIN_FILE`` en production : le fichier doit exister (créé par
``scripts/lody-auth-set-password.sh``) avant que ce service serve du trafic authentifié.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from lody_auth.server import serve

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> int:
    host = os.environ.get("LODY_AUTH_HOST", "0.0.0.0")
    port = int(os.environ.get("LODY_AUTH_PORT", "8602"))
    admin_path = Path(os.environ.get("LODY_AUTH_ADMIN_FILE", "/secrets/admin-auth.json"))
    # En local (hors HTTPS), LODY_AUTH_COOKIE_SECURE=0 permet de tester sans TLS. En production, nginx termine
    # toujours le TLS : ce drapeau doit y rester à sa valeur par défaut (sécurisé).
    cookie_secure = os.environ.get("LODY_AUTH_COOKIE_SECURE", "1").strip().lower() not in {"0", "false", "no"}
    if not admin_path.is_file():
        logging.getLogger("lody_auth").warning(
            "%s introuvable : aucune connexion ne pourra réussir tant que l'administrateur n'a pas lancé "
            "scripts/lody-auth-set-password.sh", admin_path)
    server = serve(host=host, port=port, admin_path=admin_path, cookie_secure=cookie_secure)
    logging.getLogger("lody_auth").info("écoute sur %s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
