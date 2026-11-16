# -*- coding: utf-8 -*-
__title__ = "Rodapés por\nFamília (Perfis)"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Modela automaticamente rodapés por família baseada em linha contornando os ambientes selecionados.

Diferenciais:
- Suporta famílias carregadas no projeto (OC_RODAPÉ* Embutido, Invertido Perfil L, Reto, Santa Luzia Neo 30/43 etc.) e perfis paramétricos gerados pelo AutoBIM (Boleado, Chanfrado, Friso Duplo).
- Alinha automaticamente à cota superior do piso acabado do ambiente.
- Desconta Portas e Janelas que alcançam a altura do rodapé.
- Aplica esquadria de canto 45° (OC_Ângulo CA/CB) e corte reto 0° junto a portas/aberturas.
- Executa corte com vazio na parede hospedeira (AddInstanceVoidCut) para rodapés embutidos e invertidos."""

import os
import sys

_lib = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "lib"))
if _lib not in sys.path:
    sys.path.insert(0, _lib)

from Snippets._finishes_ceilings import run_rodapes_familia

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

run_rodapes_familia(doc, uidoc, view)
