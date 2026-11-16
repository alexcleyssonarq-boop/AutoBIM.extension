# -*- coding: utf-8 -*-
__title__ = "Unir: selecionados"
__author__ = "AutoBIM"
__version__ = "Versão 1.0"
__doc__ = """Une e corrige a ordem de corte automaticamente dos elementos selecionados pelo usuário.

Seleção Direta:
Selecione os elementos desejados (ou abra uma caixa de seleção) e o comando unirá todos os pares que se interceptam seguindo a hierarquia estrutural e arquitetônica."""

import Snippets._auto_join as aj
try:
    reload(aj)
except Exception:
    pass

aj.run(aj.MODO_SELECIONADOS)
