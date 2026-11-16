# AutoBIM — Extensão pyRevit para Autodesk Revit

Conjunto com **40 ferramentas automatizadas** divididas em 3 painéis no Revit (**2022 a 2026**):

- **Cotas Automáticas (7)**: Cota por Linha, Cotar por Ambiente, Cotar Paredes Selecionadas, Cotar Parede e Aberturas, e menu *Cotas em Corte* (Cota Rápida no Corte, Pé-Direito por Ambiente, Cotas de Esquadrias).
- **Modelagem (23)**: Piso por Ambiente (Sem Tratamento / Soleira / Contínuo), Forros por Ambiente, Soleiras e Peitoris, Paginação de Piso, Altura de Paredes em Lote, Revestimento e Rodapés (famílias `OC_RODAPÉ*` + corte por vazio), Gesso e Molduras (Tabicas, Cortineiros, Sancas, Rodameios), Estrutura de Cobertura (Trama, Vidraça Inclinada, Tesouras, Pontaletes, Estrutura Metálica 3D/Vidraça, Treliças e Lista de Corte/Quantitativo com Tabela Revit + CSV) e Unir Geometria.
- **Documentação (10)**: Quadro de Acabamentos + Legenda Gráfica 1:1, Renumeração por Clique e em Lote, Tag Automática em Lote, Pé-Direito Automático para Tags (`PD = 2,80m` / `PD = 2,60 / 3,40m`), Vistas por Ambiente (2D/3D/Elevações), Centralizar Tags, Cotas de Nível (Planta e Corte) e Pranchas Auto.

---

## Requisitos

- **Autodesk Revit** (testado em 2022–2026)
- **pyRevit** instalado ([pyrevitlabs.io](https://www.pyrevitlabs.io/))

---

## Como Instalar (para Colegas)

### Opção A — Instalador em 1 Clique (Windows, sem precisar de Git)
1. Baixe e extraia (ou baixe direto) a pasta `installer/` (ou clique em **Code → Download ZIP**).
2. Execute **`installer/Instalar_AutoBIM.bat`** (duplo clique).
3. Abra o Revit (ou clique em **pyRevit → Reload**). A aba **AutoBIM** aparecerá automaticamente.

### Opção B — Via pyRevit CLI (se tiver Git instalado)
```powershell
pyrevit extensions add ui AutoBIM https://github.com/alexcleyssonarq-boop/AutoBIM.extension.git
```

---

## Atualizações Automáticas

O **AutoBIM** verifica automaticamente atualizações no GitHub ao iniciar o Revit (em segundo plano, sem atrasar a abertura). Quando houver versão nova, ele oferece a atualização em 1 clique preservando todas as suas preferências locais (`*_settings.json`).

Você também pode verificar manualmente pelo botão **Documentação → Sobre / Atualizar** dentro do Revit.
