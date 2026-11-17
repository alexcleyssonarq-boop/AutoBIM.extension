# AutoBIM — Extensão pyRevit para Autodesk Revit

Conjunto com **43 ferramentas automatizadas** divididas em 4 painéis no Revit (**2022 a 2026**):

- **AutoBIM (1)**: Sobre e Atualizações (verificação automática no GitHub e atualização em 1 clique).
- **Modelagem (23)**: Piso por Ambiente (Sem Tratamento / Soleira / Contínuo), Forros por Ambiente, Soleiras e Peitoris, Paginação de Piso, Altura de Paredes em Lote, Revestimento e Rodapés (perfis paramétricos AutoBIM e famílias lineares do projeto com corte por vazio), Gesso e Molduras (Tabicas, Cortineiros, Sancas e Rodameios), Estrutura de Cobertura (Trama, Vidraça Inclinada, Tesouras, Pontaletes, Estrutura Metálica 3D/Vidraça, Treliças e Lista de Corte/Quantitativo com Tabela Revit + CSV) e Unir Geometria.
- **Cotas Automáticas (9)**: Cota por Linha, Cotar por Ambiente, Cotar Paredes Selecionadas, Cotar Parede e Aberturas, e menu *Cotas em Corte* (Cota Automática no Corte em 1 clique na vertical, Cotar Seleção no Corte levando alturas para fora do desenho, Cota Rápida no Corte, Pé-Direito por Ambiente/Vão com suporte a telhado/madeiramento e Cotas de Esquadrias).
- **Documentação (10)**: Quadro de Acabamentos + Legenda Gráfica 1:1, Renumeração por Clique e em Lote, Tag Automática em Lote, Pé-Direito Automático para Tags (`PD = 2,80m` / `PD = 2,60 / 3,40m`), Vistas por Ambiente (2D/3D/Elevações), Centralizar Tags, Cotas de Nível (Planta e Corte) e Pranchas Auto.

---

## Requisitos

- **Autodesk Revit** (testado em 2022–2026)
- **pyRevit** instalado ([pyrevitlabs.io](https://www.pyrevitlabs.io/))

---

## Como Instalar

### Opção A — Instalador `.exe` (Recomendado, Windows)
1. Acesse a última versão em **[Releases](https://github.com/alexcleyssonarq-boop/AutoBIM.extension/releases/latest)** aqui no GitHub.
2. Em **Assets**, baixe o instalador **`AutoBIM_Setup_vX.Y.Z.exe`**.
3. Dê duplo clique no `.exe` e siga o assistente (*ele verifica automaticamente a versão do pyRevit instalada no computador e instala na pasta correta*).
4. Abra o Revit (ou clique em **pyRevit → Reload**). A aba **AutoBIM** aparecerá automaticamente.

### Opção B — Instalador em 1 Clique (`.bat`, sem `.exe`)
1. Clique em **Code → Download ZIP** e extraia os arquivos (ou baixe a pasta `installer/`).
2. Execute **`installer/Instalar_AutoBIM.bat`** (duplo clique).
3. Abra o Revit (ou clique em **pyRevit → Reload**). A aba **AutoBIM** aparecerá automaticamente.

---

## Atualizações Automáticas

O **AutoBIM** verifica automaticamente atualizações no GitHub ao abrir o Revit (em segundo plano, sem atrasar a inicialização). Quando houver versão nova, ele oferece a atualização em 1 clique preservando todas as suas preferências locais (`*_settings.json`).

Você também pode verificar manualmente pelo botão **AutoBIM → Sobre e Atualizar** dentro do Revit.
