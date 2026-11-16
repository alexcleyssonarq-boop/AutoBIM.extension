# -*- coding: utf-8 -*-
__title__ = "Cotas de Nível:\nem Planta"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Insere Cotas de Nível automaticamente em Plantas Baixas logo abaixo das Tags de Ambiente (ajustando pela altura real da Tag) ou por deslocamento do centro do ambiente, sempre sobre o piso acabado superior."""

import traceback
from pyrevit import forms
import Snippets._doc_extra as de
try:
    reload(de)
except Exception:
    pass

try:
    de.run_cotas_nivel_planta()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Cotas de Nível: em Planta")
