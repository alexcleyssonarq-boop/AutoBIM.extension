# -*- coding: utf-8 -*-
__title__ = "Tag Automática\nem Lote"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Insere Tags em lote (Portas, Janelas, Ambientes, Paredes, Mobiliário ou Estrutura) com verificação inteligente anti-sobreposição, ignorando itens já identificados e respeitando alinhamento."""

import traceback
from pyrevit import forms
import Snippets._doc_tools as dt
try:
    reload(dt)
except Exception:
    pass

try:
    dt.run_tag_automatica()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Tag Automática")
