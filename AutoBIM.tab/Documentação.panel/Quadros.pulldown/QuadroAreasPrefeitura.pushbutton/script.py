# -*- coding: utf-8 -*-
__title__ = "Quadro Áreas\nPrefeitura"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Calcula áreas construídas por pavimento, área do terreno, área permeável e índices urbanísticos (Taxa de Ocupação, Coeficiente de Aproveitamento e Taxa de Permeabilidade), gerando Quadro Gráfico em Vista de Desenho e CSV padrão BR."""

import traceback
from pyrevit import forms
import Snippets._doc_prefeitura as dp
try:
    reload(dp)
except Exception:
    pass

try:
    dp.run_quadro_areas_prefeitura()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Quadro de Áreas de Prefeitura")
