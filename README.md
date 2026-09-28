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

## O que cada dashboard mostra

- **Custo de visualização:** custo por view (3s), por view de 25%, 50% e 75%, custo por engajamento
- **Atenção e retenção:** hook rate, retenção, tempo médio assistido, custo por ThruPlay e curva de retenção
- **Entrega:** investimento, alcance, impressões, frequência, CPM
- **Dia a dia:** views de 3s e hook rate nos últimos 30 dias
- **Campanhas:** tabela com as métricas de distribuição por campanha
- **Criativos:** ranking ordenável (custo por view, hook rate, retenção, custo por engajamento), com link pro post no Instagram e prévia do anúncio

Últimos 7 ou 30 dias (botão no topo), comparados com o período anterior. O dia de hoje não entra.

**Regra fixa:** só entram campanhas com `DISTRIBUICAO` no nome. O filtro está no código (`FILTRO` em `gerar.py`), não é configurável, e o gerador aborta se algum dado fugir da regra.

**Definições:** hook rate = views de 3s ÷ impressões. Retenção = ThruPlays ÷ views de 3s.

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
