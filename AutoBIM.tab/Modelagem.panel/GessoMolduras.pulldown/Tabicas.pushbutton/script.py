# -*- coding: utf-8 -*-
__title__ = "Tabicas\n(Forro Solto)"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Gera automaticamente tabicas metálicas ou em perfil ao longo das bordas de forros soltos (pé-solto).

Diferenciais:
- Mede a folga real entre a borda do Forro e a face interna da Parede adjacente e gera família AutoBIM adaptativa ao vão medido (L ou Z), ou usa famílias de tabica carregadas no projeto.
- Ignora bordas livres sem parede adjacente (como vãos abertos ou cortineiros).
- Executa AddInstanceVoidCut automaticamente quando a família possuir elemento de corte."""

import os
import sys

_lib = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "lib"))
if _lib not in sys.path:
    sys.path.insert(0, _lib)

from Snippets._finishes_ceilings import run_tabicas

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

run_tabicas(doc, uidoc, view)
