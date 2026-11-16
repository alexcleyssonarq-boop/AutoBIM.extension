# -*- coding: utf-8 -*-
__title__ = "Cota Rápida\nno Corte"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Posiciona uma cadeia contínua de cotas no Corte ou Elevação a partir de dois cliques (snap desligado).

- Linha vertical: cota base da laje, topo de piso acabado, face inferior de forros, vergas/vigas, topo de paredes cortadas e cobertura sólida (ignorando vidraças inclinadas e esquadrias).
- Linha horizontal: cota espessuras de paredes e pilares cortados."""

import traceback
from pyrevit import forms
import Snippets._section_dims as sd
try:
    reload(sd)
except Exception:
    pass

try:
    sd.run_cotar_corte_linha()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Cota Rápida no Corte")
