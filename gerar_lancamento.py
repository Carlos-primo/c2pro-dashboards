"""Gera os dashboards de CAPTACAO de lancamento pago (Meta Ads) direto da API da Meta, sem Claude no meio.

Cada pasta lancamento/<slug>/ com um config.json vira um dashboard em site/lancamento/<slug>/index.html,
publicado em https://carlos-primo.github.io/c2pro-dashboards/lancamento/<slug>/

Uso local:   python gerar_lancamento.py                 (todos os lancamentos)
             python gerar_lancamento.py rafaela-lp05    (so um)

Lancamento pago = venda de ingresso, entao o funil e o mesmo do dashboard de vendas:
impressoes (CPM) > cliques no link (CTR) > visitas a pagina (connect rate)
> checkouts (pagina > checkout) > compras (checkout > compra). Cadastros (lead) aparecem a parte.

config.json:
  nome, subtitulo, conta_anuncio, criativos_gasto_minimo
  lancamento   codigo do lancamento no nome das campanhas (ex: "LP05")
  inicio       primeiro dia do lancamento (AAAA-MM-DD); "Lancamento todo" vai dele ate ontem
  fim          opcional: ultimo dia, quando a captacao acabar (os periodos param nele)
  apelidos     opcional: {"NOME_NO_GERENCIADOR": "Nome que o cliente entende"} pra publicos/campanhas
  filtro_campanha  opcional: troca o termo CAPTACAO quando o cliente nomeia as campanhas de outro jeito
                   (ex: "ICP" no lancamento do Athila). Sem ele, vale CAPTACAO

Regra: so entram campanhas com CAPTACAO (ou o filtro_campanha do config) e o codigo do lancamento no nome.

Variaveis de ambiente: as mesmas do gerar.py (META_ADS_TOKEN, SENHA_LANCAMENTO_<SLUG> opcional).
"""

import datetime as dt
import json
import os
import shutil
import sys
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

from gerar import api, criptografar, diario_seguro

AQUI = Path(__file__).resolve().parent
SITE = AQUI / "site" / "lancamento"
CFG = {}

# Regra da agencia: dashboard de lancamento so mostra campanhas de CAPTACAO daquele lancamento.
# CAPTACAO e o padrao; o config pode trocar por outro termo (filtro_campanha) quando o cliente nomeia
# as campanhas de outro jeito. O codigo do lancamento (LP05 etc.) vem do config.
FILTRO_PADRAO = "CAPTACAO"


def filtros():
    """Termos que o nome da campanha precisa conter, sem repetir."""
    termos = [CFG.get("filtro_campanha") or FILTRO_PADRAO, CFG["lancamento"]]
    return list(dict.fromkeys(sem_acento(t) for t in termos))

PERIODOS = [1, 3, 7]

CAMPOS_METRICAS = "spend,impressions,reach,inline_link_clicks,actions,action_values"
CAMPOS_LINHA = "spend,impressions,inline_link_clicks,actions,action_values"

# A Meta devolve o mesmo evento com nomes diferentes conforme a origem; usa o primeiro que existir.
EVENTOS = {
    "visitas": ["landing_page_view", "omni_landing_page_view"],
    "cadastros": ["lead", "offsite_conversion.fb_pixel_lead", "omni_lead"],
    "checkouts": ["initiate_checkout", "omni_initiated_checkout", "offsite_conversion.fb_pixel_initiate_checkout"],
    "compras": ["purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"],
}


def sem_acento(txt):
    return unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode().upper()


def eh_do_lancamento(nome_campanha):
    nome = sem_acento(nome_campanha)
    return all(t in nome for t in filtros())


def conferir(condicao, mensagem):
    """Aborta a geracao em vez de publicar um dashboard com dado fora da regra."""
    if not condicao:
        sys.exit(f"ERRO de consistencia ({CFG.get('nome')}): {mensagem}")


def insights(nivel, inicio, fim, campos, **extra):
    return api(
        f"{CFG['conta_anuncio']}/insights",
        level=nivel,
        fields=campos,
        time_range=json.dumps({"since": inicio.isoformat(), "until": fim.isoformat()}),
        filtering=json.dumps([{"field": "campaign.name", "operator": "CONTAIN", "value": t} for t in filtros()]),
        limit=500,
        **extra,
    )


def primeiro(dic, chaves):
    for k in chaves:
        if k in dic:
            return dic[k]
    return 0.0


def metricas(linha, com_alcance=True):
    acoes = {a["action_type"]: float(a["value"]) for a in linha.get("actions", [])}
    valores = {a["action_type"]: float(a["value"]) for a in linha.get("action_values", [])}
    m = {
        "gasto": round(float(linha.get("spend", 0)), 2),
        "impressoes": int(linha.get("impressions", 0)),
        "cliques": float(linha.get("inline_link_clicks", 0)),
        "visitas": primeiro(acoes, EVENTOS["visitas"]),
        "cadastros": primeiro(acoes, EVENTOS["cadastros"]),
        "checkouts": primeiro(acoes, EVENTOS["checkouts"]),
        "compras": primeiro(acoes, EVENTOS["compras"]),
        "faturamento": round(primeiro(valores, EVENTOS["compras"]), 2),
    }
    if com_alcance:
        m["alcance"] = int(linha.get("reach", 0))
    return m


def conta_total(inicio, fim):
    linhas = insights("account", inicio, fim, CAMPOS_METRICAS)
    return metricas(linhas[0]) if linhas else metricas({})


def linhas_anuncio(inicio, fim, **extra):
    """Uma linha por anuncio (com campanha e publico), base de todos os filtros do dashboard."""
    linhas = []
    campos = "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name," + CAMPOS_LINHA
    if extra.get("time_increment") == 1:
        brutas = diario_seguro(lambda c, i, f, **e: insights("ad", i, f, c, **e), campos, inicio, fim)
    else:
        brutas = insights("ad", inicio, fim, campos, **extra)
    for l in brutas:
        conferir(eh_do_lancamento(l["campaign_name"]), f"anuncio de campanha fora do filtro: {l['campaign_name']}")
        m = metricas(l, com_alcance=False)
        if m["gasto"] > 0 or m["impressoes"] > 0:
            linha = {"ad": l["ad_id"], "cr": l["ad_name"], "pb": l["adset_id"], "cp": l["campaign_id"], **m}
            if "date_start" in l:
                linha["data"] = l["date_start"]
            linhas.append(linha)
            NOMES["publicos"][l["adset_id"]] = l["adset_name"]
            NOMES["campanhas"][l["campaign_id"]] = l["campaign_name"]
    return linhas


NOMES = {"publicos": {}, "campanhas": {}}


def periodo(inicio, fim, comparar):
    """comparar=False quando o periodo anterior cairia antes do lancamento comecar."""
    dias = (fim - inicio).days + 1
    ant_fim = inicio - dt.timedelta(days=1)
    ant_inicio = ant_fim - dt.timedelta(days=dias - 1)

    linhas = linhas_anuncio(inicio, fim)
    atual = conta_total(inicio, fim)
    soma = sum(l["gasto"] for l in linhas)
    conferir(abs(soma - atual["gasto"]) < 0.05 + 0.01 * len(linhas),
             f"total {inicio}..{fim} (R$ {atual['gasto']:.2f}) difere da soma dos anuncios (R$ {soma:.2f})")

    p = {"inicio": inicio.isoformat(), "fim": fim.isoformat(), "atual": atual, "linhas": linhas,
         "ant_inicio": None, "ant_fim": None, "anterior": None, "linhas_ant": None}
    if comparar:
        p.update({"ant_inicio": ant_inicio.isoformat(), "ant_fim": ant_fim.isoformat(),
                  "anterior": conta_total(ant_inicio, ant_fim), "linhas_ant": linhas_anuncio(ant_inicio, ant_fim)})
    return p


def detalhes_anuncios(ids):
    """Status, miniatura e links do criativo (Instagram e previa), 50 por chamada."""
    info = {}
    for i in range(0, len(ids), 50):
        lote = ids[i:i + 50]
        filtro = [{"field": "ad.id", "operator": "IN", "value": lote}]
        for ad in api(f"{CFG['conta_anuncio']}/ads",
                      fields="effective_status,preview_shareable_link,"
                             "creative{thumbnail_url,instagram_permalink_url}",
                      filtering=json.dumps(filtro), limit=50):
            criativo = ad.get("creative", {})
            info[ad["id"]] = {"status": ad.get("effective_status", ""),
                              "thumb": criativo.get("thumbnail_url", ""),
                              "instagram": criativo.get("instagram_permalink_url", ""),
                              "previa": ad.get("preview_shareable_link", "")}
    return info


def gerar_cliente(slug):
    CFG.clear()
    NOMES["publicos"].clear()
    NOMES["campanhas"].clear()
    CFG.update(json.loads((AQUI / "lancamento" / slug / "config.json").read_text(encoding="utf-8")))
    saida = SITE / slug

    conta = api(CFG["conta_anuncio"], fields="name,currency,timezone_name")
    fuso = ZoneInfo(conta.get("timezone_name") or "America/Sao_Paulo")
    agora = dt.datetime.now(fuso)
    ontem = agora.date() - dt.timedelta(days=1)
    inicio_lanc = dt.date.fromisoformat(CFG["inicio"])
    fim_lanc = min(ontem, dt.date.fromisoformat(CFG["fim"])) if CFG.get("fim") else ontem
    conferir(fim_lanc >= inicio_lanc, f"o lancamento comeca em {inicio_lanc}, ainda nao tem dia fechado")

    periodos = {}
    for d in PERIODOS:
        ini = fim_lanc - dt.timedelta(days=d - 1)
        if ini < inicio_lanc:
            continue
        ant_ini = ini - dt.timedelta(days=d)
        periodos[str(d)] = periodo(ini, fim_lanc, comparar=ant_ini >= inicio_lanc)
    periodos["todo"] = periodo(inicio_lanc, fim_lanc, comparar=False)

    # Dia a dia do lancamento: total da conta e por anuncio (pro funil filtrado).
    por_dia = {l["date_start"]: metricas(l) for l in diario_seguro(
        lambda c, i, f, **e: insights("account", i, f, c, **e), CAMPOS_METRICAS, inicio_lanc, fim_lanc)}
    n_dias = (fim_lanc - inicio_lanc).days + 1
    diario = [{"data": (inicio_lanc + dt.timedelta(days=n)).isoformat(),
               **por_dia.get((inicio_lanc + dt.timedelta(days=n)).isoformat(), metricas({}))}
              for n in range(n_dias)]
    diario_anuncios = linhas_anuncio(inicio_lanc, fim_lanc, time_increment=1)
    conferir(abs(sum(d["gasto"] for d in diario) - periodos["todo"]["atual"]["gasto"]) < 0.05,
             "serie diaria nao bate com o total do lancamento")

    ids = sorted({l["ad"] for p in periodos.values() for l in (p["linhas"] + (p["linhas_ant"] or []))})
    status_camp = {c["id"]: c.get("effective_status", "") for c in api(
        f"{CFG['conta_anuncio']}/campaigns", fields="effective_status", limit=500)}
    status_pub = {c["id"]: c.get("effective_status", "") for c in api(
        f"{CFG['conta_anuncio']}/adsets", fields="effective_status", limit=500)}
    apelidos = CFG.get("apelidos", {})

    dados = {
        "nome": CFG["nome"],
        "subtitulo": CFG.get("subtitulo", ""),
        "lancamento": CFG["lancamento"],
        "inicio": inicio_lanc.isoformat(),
        "fim_lancamento": CFG.get("fim"),
        "conta": conta.get("name", ""),
        "moeda": conta.get("currency", "BRL"),
        "filtro": " + ".join(filtros()),
        "gerado_em": agora.isoformat(timespec="minutes"),
        "gasto_minimo_criativo": CFG["criativos_gasto_minimo"],
        "campanhas": {i: {"nome": apelidos.get(n, n), "original": n, "status": status_camp.get(i, "")}
                      for i, n in NOMES["campanhas"].items()},
        "publicos": {i: {"nome": apelidos.get(n, n), "original": n, "status": status_pub.get(i, "")}
                     for i, n in NOMES["publicos"].items()},
        "anuncios": detalhes_anuncios(ids),
        "periodos": periodos,
        "diario": diario,
        "diario_anuncios": diario_anuncios,
    }

    json_dados = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    senha = os.environ.get(f"SENHA_LANCAMENTO_{slug.upper().replace('-', '_')}", "")
    payload = criptografar(json_dados, senha) if senha else {"enc": False, "dados": dados}

    saida.mkdir(parents=True, exist_ok=True)
    html = (AQUI / "template_lancamento.html").read_text(encoding="utf-8")
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    (saida / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(AQUI / "logo.png", saida / "logo.png")

    t = periodos["todo"]["atual"]
    print(f"OK lancamento/{slug}: {inicio_lanc} a {fim_lanc} R$ {t['gasto']:.2f}, {t['compras']:.0f} compras, "
          f"{len(NOMES['campanhas'])} campanhas, {len(NOMES['publicos'])} publicos, {len(ids)} anuncios | "
          f"{'com senha' if senha else 'link aberto, sem senha'}")


def main():
    todos = sorted(p.parent.name for p in (AQUI / "lancamento").glob("*/config.json"))
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
        '<title>C2Pro</title><body style="background:#05070A"></body>', encoding="utf-8")
    falhas = []
    for slug in sys.argv[1:] or todos:
        # Um cliente com erro (token sem acesso, consistencia) nao derruba os outros: fica de fora
        # do site nesta rodada e aparece como erro no log do GitHub Actions.
        try:
            gerar_cliente(slug)
        except SystemExit as erro:
            falhas.append(slug)
            print(f"::error::{slug} ficou de fora: {erro}")
    if falhas and sys.argv[1:]:
        sys.exit(1)


if __name__ == "__main__":
    main()
