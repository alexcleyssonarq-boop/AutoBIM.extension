# -*- coding: utf-8 -*-
__title__ = "Piso: sem tratamento"
__author__ = "AutoBIM"
__version__ = "Versão 1.1"
__doc__ = """Cria pisos pelo contorno dos ambientes selecionados.

Sem tratamento de portas:
O piso para na face da parede, sem avançar pelo vão das portas ou aberturas.

Pisos existentes não são removidos nem substituídos."""

import Snippets._floors_by_room as fbr
try:
    reload(fbr)
except Exception:
    pass

fbr.run(fbr.MODO_SEM)
