# -*- coding: utf-8 -*-
__title__ = "Cotas de\nEsquadrias"
__author__ = "AutoBIM"
__version__ = "Versão 2.0"
__doc__ = """Cria cotas verticais automáticas para Portas e Janelas visíveis em Cortes e Elevações (Peitoril, Altura e Boneca superior até Forro/Laje) e, opcionalmente, largura frontal."""

import traceback
from pyrevit import forms
import Snippets._section_dims as sd
try:
    reload(sd)
except Exception:
    pass

try:
    sd.run_cotar_esquadrias_corte(abrir_config=True)
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Cotas de Esquadrias")
