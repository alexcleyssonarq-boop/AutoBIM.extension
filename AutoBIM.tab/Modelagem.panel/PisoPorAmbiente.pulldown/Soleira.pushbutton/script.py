# -*- coding: utf-8 -*-
__title__ = "Piso: soleira separada"
__author__ = "AutoBIM"
__version__ = "Versão 1.1"
__doc__ = """Cria pisos pelo contorno dos ambientes selecionados, com soleira separada nos vãos.

Soleira separada:
Cria um piso à parte em cada vão de porta ou abertura, cobrindo a espessura da parede e o vão bruto completo (incluindo forras/marcos), permitindo escolher um tipo de piso/material próprio para a soleira.

Quando dois ambientes selecionados dividem o mesmo vão, a soleira fica com o ambiente de maior área.
Pisos existentes não são removidos nem substituídos."""

import Snippets._floors_by_room as fbr
try:
    reload(fbr)
except Exception:
    pass

fbr.run(fbr.MODO_SOLEIRA)
