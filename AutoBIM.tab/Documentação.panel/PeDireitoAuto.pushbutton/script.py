# -*- coding: utf-8 -*-
__title__ = "Pé-Direito\nAutomático"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Mede automaticamente o pé-direito livre por Ambiente (Room), identificando Forro reto/inclinado ou, na ausência de forro, a face inferior do Madeiramento (caibros/terças) / Telhado / Laje, gravando o texto (ex.: 'PD = 2,80m' ou 'PD = 2,60 / 3,40m') para exibição na Tag de Ambiente."""

import traceback
from pyrevit import forms
import Snippets._ceiling_height as ch
try:
    reload(ch)
except Exception:
    pass

try:
    ch.run_pe_direito_auto()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Pé-Direito Automático")
