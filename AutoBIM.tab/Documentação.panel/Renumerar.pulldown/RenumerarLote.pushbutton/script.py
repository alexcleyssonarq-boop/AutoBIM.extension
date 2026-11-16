# -*- coding: utf-8 -*-
__title__ = "Renumerar Automático\nem Lote (Tabela)"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Renumera Portas, Janelas, Ambientes e outros elementos automaticamente em lote sem precisar clicar, ordenando por Largura, Altura, Descrição, Comentários ou importando os critérios de uma Tabela (Schedule) existente, com pré-visualização antes de aplicar."""

import traceback
from pyrevit import forms
import Snippets._doc_tools as dt
try:
    reload(dt)
except Exception:
    pass

try:
    dt.run_renumerar_lote()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Renumerar Automático em Lote")
