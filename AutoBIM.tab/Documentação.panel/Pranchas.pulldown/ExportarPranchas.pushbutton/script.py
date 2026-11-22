# -*- coding: utf-8 -*-
__title__ = "Exportar\nPranchas"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Exporta múltiplas pranchas em DWG Único (unindo abas de Layout e Model lado a lado via AutoCAD accoreconsole.exe quando instalado, com fallback para lote DWG) e/ou Lote PDF padronizado."""

import traceback
from pyrevit import forms
import Snippets._doc_sheets as ds
try:
    reload(ds)
except Exception:
    pass

try:
    ds.run_exportar_pranchas()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Exportar Pranchas")
