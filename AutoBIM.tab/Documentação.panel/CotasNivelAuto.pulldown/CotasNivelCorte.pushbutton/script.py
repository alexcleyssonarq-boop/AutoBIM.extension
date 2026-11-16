# -*- coding: utf-8 -*-
__title__ = "Cotas de Nível:\nem Corte"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Insere Cotas de Nível automaticamente em Cortes e Elevações centralizadas em cada Ambiente cortado (mesmo compartilhando o piso) e identificando o piso acabado sobre a laje, além de telhados."""

import traceback
from pyrevit import forms
import Snippets._doc_extra as de
try:
    reload(de)
except Exception:
    pass

try:
    de.run_cotas_nivel_corte()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Cotas de Nível: em Corte")
