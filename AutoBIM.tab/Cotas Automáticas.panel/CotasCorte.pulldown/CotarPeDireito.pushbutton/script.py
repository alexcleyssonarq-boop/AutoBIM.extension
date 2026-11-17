# -*- coding: utf-8 -*-
__title__ = "Pé-Direito\npor Ambiente"
__author__ = "AutoBIM"
__version__ = "Versão 2.0"
__doc__ = """Identifica Ambientes (Rooms) ou vãos entre paredes cortados na vista ativa e cria cotas verticais automáticas de pé-direito (Piso acabado → Forro/Cobertura → Laje / Topo)."""

import traceback
from pyrevit import forms
import Snippets._section_dims as sd
try:
    reload(sd)
except Exception:
    pass

try:
    sd.run_cotar_pe_direito(abrir_config=True)
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Pé-Direito por Ambiente")
