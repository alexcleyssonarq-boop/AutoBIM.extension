# -*- coding: utf-8 -*-
__title__ = "Sancas por\nAmbiente"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Cria sancas contornando os ambientes selecionados em múltiplos sistemas construtivos:

- Sanca Fechada nativa (Parede testeira + Forro perimetral rebaixado).
- Sanca Aberta para fita LED (com aba frontal de iluminação).
- Perfis paramétricos AutoBIM baseados em linha (Chanfrada 45°, Meia-Cana Curva, Degrau Duplo).
- Famílias CurveBased carregadas do projeto."""

import os
import sys

_lib = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "lib"))
if _lib not in sys.path:
    sys.path.insert(0, _lib)

from Snippets._finishes_ceilings import run_sancas

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

run_sancas(doc, uidoc, view)
