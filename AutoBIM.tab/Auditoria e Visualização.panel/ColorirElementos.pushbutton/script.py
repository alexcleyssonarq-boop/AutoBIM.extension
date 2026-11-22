# -*- coding: utf-8 -*-
__title__ = "Colorir\nElementos"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Colore temporariamente elementos na vista ativa por Tipo, Nível, Material, Espessura, Marca ou qualquer parâmetro, com legenda visual e botão para resetar as cores em 1 clique."""

import traceback
from pyrevit import forms
import Snippets._audit_viz as av
try:
    reload(av)
except Exception:
    pass

try:
    av.run_colorir_elementos()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Colorir Elementos")
