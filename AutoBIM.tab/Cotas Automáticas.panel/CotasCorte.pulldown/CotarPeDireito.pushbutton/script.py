# -*- coding: utf-8 -*-
__title__ = "Pé-Direito\npor Ambiente"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Identifica automaticamente os Ambientes (Rooms) cortados na vista ativa e cria cotas verticais de pé-direito (Piso acabado → Forro → Laje superior), deslocadas do centro para não sobrepor Tags e Cotas de Nível."""

import traceback
from pyrevit import forms
import Snippets._section_dims as sd
try:
    reload(sd)
except Exception:
    pass

try:
    sd.run_cotar_pe_direito()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Pé-Direito por Ambiente")
