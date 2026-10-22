# Dashboards C2Pro

Dashboards de distribuição (Meta Ads) que puxam os dados direto da API da Meta, sem IA no meio.
Atualizam sozinhos todo dia às 2h (Brasília) pelo GitHub Actions e ficam publicados no GitHub Pages,
cada cliente num link fixo:

```
https://carlos-primo.github.io/c2pro-dashboards/<cliente>/
```

| Cliente | Link |
|---|---|
| C2Pro | https://carlos-primo.github.io/c2pro-dashboards/c2pro/ |
| Evaldit Rafaela | https://carlos-primo.github.io/c2pro-dashboards/evaldit-rafaela-ibama/ |
| Método NeuroappAvalia | https://carlos-primo.github.io/c2pro-dashboards/metodo-neuroapp-avalia-cartao/ |
| Amanda Elia Advogada (período fixo: 22/12/2025 a 18/02/2026) | https://carlos-primo.github.io/c2pro-dashboards/amanda-elia/ |

## O que cada dashboard de distribuição mostra

- **Custo de visualização:** custo por view (3s), por view de 25%, 50% e 75%, custo por engajamento
- **Atenção e retenção:** hook rate, retenção, tempo médio assistido, custo por ThruPlay e curva de retenção
- **Entrega:** investimento, alcance, impressões, frequência, CPM
- **Dia a dia:** views de 3s e hook rate nos últimos 30 dias
- **Campanhas:** tabela com as métricas de distribuição por campanha
- **Criativos:** ranking ordenável (custo por view, hook rate, retenção, custo por engajamento), com link pro post no Instagram e prévia do anúncio

Últimos 7 ou 30 dias (botão no topo), comparados com o período anterior. O dia de hoje não entra.

**Regra fixa:** só entram campanhas com `DISTRIBUICAO` no nome. O filtro está no código (`FILTRO` em `gerar.py`), não é configurável, e o gerador aborta se algum dado fugir da regra.

**Definições:** hook rate = views de 3s ÷ impressões. Retenção = ThruPlays ÷ views de 3s.

## Dashboards de vendas

Mesma lógica, para campanhas com `VENDAS` no nome (filtro fixo em `FILTRO` no `gerar_vendas.py`).
Cada cliente é uma pasta `vendas/<slug>/config.json` e o link fica:

```
https://carlos-primo.github.io/c2pro-dashboards/vendas/<cliente>/
```

| Cliente | Link |
|---|---|
| Ordus System | https://carlos-primo.github.io/c2pro-dashboards/vendas/ordus/ |
| Vita Hair | https://carlos-primo.github.io/c2pro-dashboards/vendas/vita-hair/ |

- **Resumo:** custo por compra, compras, investimento, faturamento, ROAS e ticket médio
- **Funil visual:** impressões (CPM) > cliques no link (CTR, CPC) > visitas à página (connect rate, custo por visita) > checkouts (página para checkout, custo por checkout) > compras (checkout para compra, custo por compra), com variação em pontos percentuais e alerta na etapa que mais caiu
- **Entrega, dia a dia, campanhas e criativos** com o funil de cada um

Ontem, últimos 3, 7 ou 30 dias, comparados com o período anterior. Compras e faturamento são os registrados pelo pixel.
Pelo Claude Code: skill `c2pro-dashboard-vendas`. Rodar no PC: `python gerar_vendas.py [slug]` e abrir `site/vendas/<slug>/index.html`.
Senha opcional: secret `SENHA_VENDAS_<SLUG>` no passo "Gerar dashboards de vendas" do workflow.

## Dashboards da Black Friday

Campanhas com `BLACK` no nome (filtro no config), separadas por conta de anuncio. Resultado principal: lead, com compras do pixel como secundario.
Cada cliente e uma pasta `black/<slug>/config.json` com a lista de `contas`, e o link fica:

```
https://carlos-primo.github.io/c2pro-dashboards/black/<cliente>/
```

| Cliente | Contas | Link |
|---|---|---|
| Wescley Lima | Camila e Revenda (C.Ad2) | https://carlos-primo.github.io/c2pro-dashboards/black/wescley/ |

Abas por conta + "Todas as contas" (alcance nao soma entre contas). Reaproveita o `gerar_vendas.py`. Rodar no PC: `python gerar_black.py [slug]`.
Senha opcional: secret `SENHA_BLACK_<SLUG>` no passo "Gerar dashboards da Black" do workflow.

## Dashboards de leads

Avaliação de leads: só volume, sem investimento, custo ou comparação com período anterior (nada disso vai pro payload).
Entram todas as campanhas da conta. Cada cliente é uma pasta `leads/<slug>/config.json` e o link fica:

```
https://carlos-primo.github.io/c2pro-dashboards/leads/<cliente>/
```

| Cliente | Link |
|---|---|
| Atento Corretora | https://carlos-primo.github.io/c2pro-dashboards/leads/atento/ |
| Paty Clar | https://carlos-primo.github.io/c2pro-dashboards/leads/paty-clar/ |

- **Resumo:** leads no período e quantos criativos geraram lead
- **Criativos que mais geraram leads:** ranking por quantidade de leads, cada criativo somado em todos os públicos em que rodou, com link pro Instagram e prévia
- **Funil geral:** impressões > cliques no link (CTR) > visitas à página, quando houver > leads (conversão em lead)

Filtro de período: últimos 7 e 30 dias. Leads são os que a Meta registrou (formulário ou pixel).
Rodar no PC: `python gerar_leads.py [slug]` e abrir `site/leads/<slug>/index.html`.
Senha opcional: secret `SENHA_LEADS_<SLUG>` no passo "Gerar dashboards de leads" do workflow.

## Dashboards de lançamento (captação de lançamento pago)

Para a captação de um lançamento pago (venda de ingresso). Entram só campanhas com `CAPTACAO` **e** o código
do lançamento no nome (ex: `LP05`). `CAPTACAO` é o padrão do `gerar_lancamento.py`; o código vem do config.
Quando o cliente nomeia as campanhas de outro jeito, o config troca `CAPTACAO` por outro termo em `filtro_campanha`
(ex: o Athila usa `ICP` em todas as campanhas de ingresso).
Cada lançamento é uma pasta `lancamento/<slug>/config.json` e o link fica:

```
https://carlos-primo.github.io/c2pro-dashboards/lancamento/<slug>/
```

| Cliente | Lançamento | Link |
|---|---|---|
| Rafaela Evaldt | LP05 (desde 02/10/2026) | https://carlos-primo.github.io/c2pro-dashboards/lancamento/rafaela-lp05/ |
| AgroFácil (Athila) | Imersão Consultor Pecuário, campanhas ICP (22/09 a 17/10/2026) | https://carlos-primo.github.io/c2pro-dashboards/lancamento/agrofacil-athila/ |

- **Captação:** custo por ingresso, ingressos, investimento, faturamento, ROAS, ticket médio, o funil de vendas
  (impressões > cliques > visitas > checkouts > ingressos), entrega, dia a dia desde o início (ingressos, checkouts e
  custo por ingresso de cada dia, com a média do lançamento), tabelas de campanhas
  e públicos (clicáveis) e ranking de criativos (cada criativo somado em todos os públicos)
- **Funil filtrado:** escolhe campanha, público e/ou criativo (combináveis) e vê o funil, os números e os ingressos
  por dia só daquilo, com tabela pra comparar lado a lado. Link direto: `.../lancamento/<slug>/#filtro`
- **Distribuição** (com `"distribuicao": true` no config): as métricas do dashboard de distribuição (só campanhas
  `DISTRIBUICAO`) nos mesmos períodos do lançamento. Link direto: `.../#distribuicao`
- **Geral** (com `vendas_reais` no config): tudo que a conta investiu no período, campanha por campanha (captação,
  distribuição e outras), contra os ingressos vendidos da planilha. Link direto: `.../#geral`

**Vendas reais** (`vendas_reais` no config): o total de ingressos do funil vem de `lancamento/<slug>/vendas.json`
(contagem por dia tirada da planilha de vendas do cliente, sem e-mail nem telefone, porque o repositório é público), e o
faturamento passa a ser ingressos × `ticket_liquido` (bruto = ingressos × `preco`). ROAS = faturamento líquido ÷
investimento. Por campanha, público e criativo continua o pixel (só ele sabe de onde veio cada venda). Dias depois do
`ate` do `vendas.json` usam o pixel até a planilha ser atualizada (o dashboard avisa). Pra atualizar: contar de novo os
e-mails únicos da planilha, regravar o `vendas.json` e dar push.

Períodos: ontem, últimos 3 e 7 dias e "lançamento todo" (do `inicio` até ontem). Só compara com o período anterior
quando ele cabe dentro do lançamento. Cadastros (lead) aparecem à parte, fora do funil.

config.json: `nome`, `subtitulo`, `conta_anuncio`, `lancamento` (código no nome das campanhas), `inicio` (AAAA-MM-DD),
`criativos_gasto_minimo`, `fim` opcional (quando a captação acabar), `filtro_campanha` opcional e `apelidos` opcional
(`{"NOME_NO_GERENCIADOR": "nome que o cliente entende"}` para públicos e campanhas), `vendas_reais` opcional
(`{"arquivo": "vendas.json", "ticket_liquido": 102.93, "preco": 117}`) e `distribuicao` opcional (`true`).
Rodar no PC: `python gerar_lancamento.py [slug]` e abrir `site/lancamento/<slug>/index.html`.
Senha opcional: secret `SENHA_LANCAMENTO_<SLUG>` no passo "Gerar dashboards de lançamento" do workflow.

## Adicionar um cliente

Pelo Claude Code, no workspace da agência: skill `c2pro-dashboard-distribuicao` (é só dizer o nome do cliente). Manualmente:

1. Criar `clientes/<slug>/config.json` (copiar o da C2Pro e trocar):
   - `nome`, `subtitulo`
   - `conta_anuncio`: `act_...` do cliente
   - `criativos_gasto_minimo`: gasto mínimo pro anúncio aparecer no ranking
2. Commit e push. O link fica `https://carlos-primo.github.io/c2pro-dashboards/<slug>/`
3. Pra rodar na hora: aba Actions > Atualizar dashboards > Run workflow

O token da Meta precisa ter acesso à conta de anúncio do cliente.

Senha opcional por cliente: criar o secret `SENHA_<SLUG>` (ex: `SENHA_CLIENTE_X`) e adicioná-lo no bloco `env` do passo "Gerar dashboards" em `.github/workflows/atualizar.yml`.

## Rodar no PC (teste)

```bash
pip install -r requirements.txt
python gerar.py          # todos os clientes
python gerar.py c2pro    # só um
```

Abre `site/<slug>/index.html` no navegador. Localmente o token vem do `.env` da skill meta-ads do workspace `Claude Code` ao lado.

## Token da Meta

Fica no secret `META_ADS_TOKEN` (ninguém vê, nem em repositório público). Token de usuário expira em cerca
de 60 dias. Pra não precisar trocar, use um token de **System User** do Business Manager (não expira).

## Privacidade

O repositório é público (o código não tem nenhum segredo). Os dados só existem na página publicada,
que qualquer pessoa com o link consegue abrir. A página raiz fica em branco e não lista os clientes,
e as páginas pedem para não serem indexadas pelo Google.

## Planejamentos

Páginas estáticas de planejamento estratégico de cliente (não puxam dados, não são dashboards de acompanhamento).
Cada uma fica em `planejamento/<cliente>/index.html` e o workflow só copia a pasta pro site:

```
https://carlos-primo.github.io/c2pro-dashboards/planejamento/<cliente>/
```

| Cliente | Link |
|---|---|
| Mariana Groth (Growth Compliance) | https://carlos-primo.github.io/c2pro-dashboards/planejamento/mariana-groth/ |

## Relatórios

Relatórios fechados de um período (não atualizam sozinhos). Os dados ficam dentro do próprio HTML, em
`relatorios/<cliente>/index.html`, e o workflow só copia a pasta pro site:

```
https://carlos-primo.github.io/c2pro-dashboards/relatorios/<cliente>/
```

| Cliente | Relatório | Link |
|---|---|---|
| Todos Arquitetura | Vendas de ingressos (captação 27/08 a 15/09/2026) | https://carlos-primo.github.io/c2pro-dashboards/relatorios/todos-arquitetura/ |
| BJJ Protech | Análise do fim de semana de queda (10 e 11/10/2026) | https://carlos-primo.github.io/c2pro-dashboards/relatorios/bjj-protech-fim-de-semana/ |

## Apresentações

Slides estáticos da C2Pro (pitch, palestra). Cada um fica em `apresentacoes/<nome>/index.html`, com as
imagens em `img/`, e o workflow só copia a pasta pro site. Setas ou toque pra navegar, `F` tela cheia,
`N` mostra o roteiro (ou `?roteiro` no link).

| Apresentação | Link |
|---|---|
| Pitch Dobre seus Lucros com IA (Universo Subido + Viver de IA) | https://carlos-primo.github.io/c2pro-dashboards/apresentacoes/dobre-seus-lucros-com-ia/ |
| Debriefing EARQ Mind, 1º lançamento (com senha, conteúdo criptografado) | https://carlos-primo.github.io/c2pro-dashboards/apresentacoes/earq-debriefing-l01/ |
| Resultados Atento, 13 a 22/10/2026 (com senha, conteúdo criptografado) | https://carlos-primo.github.io/c2pro-dashboards/apresentacoes/atento-resultados-out26/ |
