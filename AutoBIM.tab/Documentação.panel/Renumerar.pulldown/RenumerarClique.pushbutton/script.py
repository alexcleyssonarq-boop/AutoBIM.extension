# -*- coding: utf-8 -*-
__title__ = "Renumerar\npor Clique"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Renumera em sequência rápida (ex.: P01, P02... ou J01, J02...) Portas, Janelas, Ambientes ou outros elementos na ordem exata dos cliques na planta, atualizando o modelo e as tags ao vivo."""

import traceback
from pyrevit import forms
import Snippets._doc_tools as dt
try:
    reload(dt)
except Exception:
    pass

try:
    dt.run_renumerar_clique()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Renumerar por Clique")
