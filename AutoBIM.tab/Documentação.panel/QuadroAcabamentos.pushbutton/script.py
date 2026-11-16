# -*- coding: utf-8 -*-
__title__ = "Quadro de\nAcabamentos"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Detecta automaticamente os acabamentos de Piso (○), Parede/Revestimento (△) e Forro (□) de cada Ambiente (Room), numera cada acabamento único, preenche os parâmetros nativos da Tag do Ambiente e gera Legenda Gráfica e Tabela."""

import traceback
from pyrevit import forms
import Snippets._doc_tools as dt
try:
    reload(dt)
except Exception:
    pass

try:
    dt.run_quadro_acabamentos()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Quadro de Acabamentos")
