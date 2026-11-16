# -*- coding: utf-8 -*-
__title__ = "Cotas de\nEsquadrias"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Cota automaticamente Peitoril, Altura e Distância até Forro/Laje de Portas e Janelas visíveis em Cortes e Elevações (com opção de cota de largura em vista frontal)."""

import traceback
from pyrevit import forms
import Snippets._section_dims as sd
try:
    reload(sd)
except Exception:
    pass

try:
    sd.run_cotar_esquadrias_corte()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Cotas de Esquadrias")
