"""Tests d'exploitation (#209) : rétention, releases, sonde d'activité, livraison — sans Docker ni réseau."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT / "scripts", Path(__file__).resolve().parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
