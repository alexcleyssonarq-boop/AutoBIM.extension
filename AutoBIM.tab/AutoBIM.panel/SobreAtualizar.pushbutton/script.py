# -*- coding: utf-8 -*-
__title__ = "Sobre e\nAtualizar"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Mostra a versão instalada do AutoBIM, verifica atualizações direto no GitHub (alexcleyssonarq-boop/AutoBIM.extension) e atualiza em 1 clique sem perder suas configurações locais."""

import traceback
from pyrevit import forms
import Snippets._updater as upd
try:
    reload(upd)
except Exception:
    pass

try:
    upd.run_sobre_atualizar()
except SystemExit:
    raise
except Exception:
    forms.alert(traceback.format_exc(), title="Erro em Atualizador AutoBIM")
