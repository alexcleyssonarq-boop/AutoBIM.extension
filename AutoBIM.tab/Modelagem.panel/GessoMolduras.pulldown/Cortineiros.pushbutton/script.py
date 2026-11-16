# -*- coding: utf-8 -*-
__title__ = "Cortineiros"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Modela cortineiros automáticos sobre Janelas ou Portas-Balcão selecionadas (com sobra lateral configurável).

Opções construtivas:
- Parede Fina Suspensa (testeira de placa gesso) + recuo automático do contorno do Forro existente via SketchEditScope.
- Família AutoBIM Paramétrica Baseada em Linha (Perfil L ou Perfil U completo).
- Família CurveBased carregada do projeto."""

import os
import sys

_lib = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "lib"))
if _lib not in sys.path:
    sys.path.insert(0, _lib)

from Snippets._finishes_ceilings import run_cortineiros

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

run_cortineiros(doc, uidoc, view)
