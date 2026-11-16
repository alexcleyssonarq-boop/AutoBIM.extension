# -*- coding: utf-8 -*-
__title__ = "Rodameios /\nBoiserie"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Modela rodameios e molduras horizontais de parede contornando os ambientes selecionados na elevação configurada.

Diferenciais:
- Suporta famílias carregadas do projeto (OC_RODAMEIO* / OC_Boiserie*), perfis não-retos gerados pelo AutoBIM (Chanfrado, Boleado, Friso Duplo) e Parede Fina nativa com JoinGeometry.
- Desconta automaticamente todas as Portas e Janelas que cruzam a elevação da moldura."""

import os
import sys

_lib = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "lib"))
if _lib not in sys.path:
    sys.path.insert(0, _lib)

from Snippets._finishes_ceilings import run_rodameios

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

run_rodameios(doc, uidoc, view)
