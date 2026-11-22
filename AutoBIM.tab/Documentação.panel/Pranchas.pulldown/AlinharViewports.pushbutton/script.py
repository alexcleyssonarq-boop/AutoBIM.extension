# -*- coding: utf-8 -*-
__title__ = "Alinhar Vistas\ne Títulos"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Alinha vistas (Viewports) entre pranchas diferentes na mesma coordenada exata (por origem do modelo, centro ou canto) e padroniza a posição e o comprimento da linha do título da vista."""

import traceback
from pyrevit import forms
import Snippets._doc_sheets as ds
try:
    reload(ds)
except Exception:
    pass

try:
    ds.run_alinhar_viewports()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Alinhar Vistas e Títulos")
