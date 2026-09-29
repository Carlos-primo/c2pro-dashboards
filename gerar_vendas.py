"""Gera os dashboards de VENDAS (Meta Ads) direto da API da Meta, sem Claude no meio.

Cada pasta vendas/<slug>/ com um config.json vira um dashboard em site/vendas/<slug>/index.html,
publicado em https://carlos-primo.github.io/c2pro-dashboards/vendas/<slug>/

Uso local:   python gerar_vendas.py            (todos os clientes de vendas)
             python gerar_vendas.py ordus      (so um)

Funil: impressoes (CPM) > cliques no link (CTR) > visitas a pagina (connect rate)
       > checkouts (pagina > checkout) > compras (checkout > compra)

Variaveis de ambiente: as mesmas do gerar.py (META_ADS_TOKEN, SENHA_VENDAS_<SLUG> opcional).
"""

import datetime as dt
import json
import os
import shutil
import sys
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

from gerar import api, criptografar

AQUI = Path(__file__).resolve().parent
SITE = AQUI / "site" / "vendas"
CFG = {}

# Regra da agencia: dashboard de vendas so mostra campanhas com VENDAS no nome.
# Fixo no codigo de proposito, nao e configuravel por cliente.
FILTRO = "VENDAS"

PERIODOS = [1, 3, 7, 30]

CAMPOS_METRICAS = "spend,impressions,reach,inline_link_clicks,actions,action_values"

# A Meta devolve o mesmo evento com nomes diferentes conforme a origem; usa o primeiro que existir.
EVENTOS = {
    "visitas": ["landing_page_view", "omni_landing_page_view"],
    "checkouts": ["initiate_checkout", "omni_initiated_checkout", "offsite_conversion.fb_pixel_initiate_checkout"],
    "compras": ["purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"],
}


def sem_acento(txt):
    return unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode().upper()


def eh_vendas(nome_campanha):
    return FILTRO in sem_acento(nome_campanha)


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
        filtering=json.dumps([{"field": "campaign.name", "operator": "CONTAIN", "value": FILTRO}]),
        limit=500,
        **extra,
    )


def primeiro(dic, chaves):
    for k in chaves:
        if k in dic:
            return dic[k]
    return 0.0


def metricas(linha):
    acoes = {a["action_type"]: float(a["value"]) for a in linha.get("actions", [])}
    valores = {a["action_type"]: float(a["value"]) for a in linha.get("action_values", [])}
    return {
        "gasto": float(linha.get("spend", 0)),
        "impressoes": int(linha.get("impressions", 0)),
        "alcance": int(linha.get("reach", 0)),
        "cliques": float(linha.get("inline_link_clicks", 0)),
        "visitas": primeiro(acoes, EVENTOS["visitas"]),
        "checkouts": primeiro(acoes, EVENTOS["checkouts"]),
        "compras": primeiro(acoes, EVENTOS["compras"]),
        "faturamento": primeiro(valores, EVENTOS["compras"]),
    }


def conta_total(inicio, fim):
    linhas = insights("account", inicio, fim, CAMPOS_METRICAS)
    return metricas(linhas[0]) if linhas else metricas({})


def detalhes_anuncios(anuncios):
    """Adiciona status, miniatura e links do criativo (Instagram e previa), 50 por chamada."""
    for i in range(0, len(anuncios), 50):
        lote = anuncios[i:i + 50]
        filtro = [{"field": "ad.id", "operator": "IN", "value": [a["id"] for a in lote]}]
        resp = {ad["id"]: ad for ad in api(
            f"{CFG['conta_anuncio']}/ads",
            fields="effective_status,preview_shareable_link,"
                   "creative{thumbnail_url,instagram_permalink_url}",
            filtering=json.dumps(filtro), limit=50)}
        for a in lote:
            info = resp.get(a["id"], {})
            criativo = info.get("creative", {})
            a["status"] = info.get("effective_status", "")
            a["thumb"] = criativo.get("thumbnail_url", "")
            a["instagram"] = criativo.get("instagram_permalink_url", "")
            a["previa"] = info.get("preview_shareable_link", "")


def periodo(dias, ontem, status_camp):
    fim = ontem
    inicio = fim - dt.timedelta(days=dias - 1)
    ant_fim = inicio - dt.timedelta(days=1)
    ant_inicio = ant_fim - dt.timedelta(days=dias - 1)

    campanhas = []
    for l in insights("campaign", inicio, fim, "campaign_id,campaign_name," + CAMPOS_METRICAS):
        conferir(eh_vendas(l["campaign_name"]), f"campanha fora do filtro: {l['campaign_name']}")
        m = metricas(l)
        if m["gasto"] > 0:
            campanhas.append({"id": l["campaign_id"], "nome": l["campaign_name"],
                              "status": status_camp.get(l["campaign_id"], ""), **m})
    campanhas.sort(key=lambda c: -c["gasto"])

    anuncios = []
    for l in insights("ad", inicio, fim, "ad_id,ad_name,campaign_name," + CAMPOS_METRICAS):
        conferir(eh_vendas(l["campaign_name"]), f"anuncio de campanha fora do filtro: {l['campaign_name']}")
        m = metricas(l)
        if m["gasto"] > 0:
            anuncios.append({"id": l["ad_id"], "nome": l["ad_name"], "campanha": l["campaign_name"], **m})

    atual = conta_total(inicio, fim)
    soma = sum(c["gasto"] for c in campanhas)
    conferir(abs(soma - atual["gasto"]) < 0.05,
             f"total {dias}d (R$ {atual['gasto']:.2f}) difere da soma das campanhas VENDAS (R$ {soma:.2f})")

    return {
        "inicio": inicio.isoformat(), "fim": fim.isoformat(),
        "ant_inicio": ant_inicio.isoformat(), "ant_fim": ant_fim.isoformat(),
        "atual": atual,
        "anterior": conta_total(ant_inicio, ant_fim),
        "campanhas": campanhas,
        "anuncios": anuncios,
    }


def serie_diaria(ontem):
    inicio = ontem - dt.timedelta(days=29)
    por_dia = {l["date_start"]: metricas(l) for l in insights(
        "account", inicio, ontem, CAMPOS_METRICAS, time_increment=1)}
    return [{"data": (inicio + dt.timedelta(days=n)).isoformat(),
             **por_dia.get((inicio + dt.timedelta(days=n)).isoformat(), metricas({}))}
            for n in range(30)]


def gerar_cliente(slug):
    CFG.clear()
    CFG.update(json.loads((AQUI / "vendas" / slug / "config.json").read_text(encoding="utf-8")))
    saida = SITE / slug

    conta = api(CFG["conta_anuncio"], fields="name,currency,timezone_name")
    fuso = ZoneInfo(conta.get("timezone_name") or "America/Sao_Paulo")
    agora = dt.datetime.now(fuso)
    ontem = agora.date() - dt.timedelta(days=1)

    status_camp = {c["id"]: c["effective_status"] for c in api(
        f"{CFG['conta_anuncio']}/campaigns", fields="effective_status", limit=500)}
    periodos = {str(d): periodo(d, ontem, status_camp) for d in PERIODOS}

    # Detalhes dos criativos uma vez so, pra todos os anuncios de todos os periodos.
    todos = {a["id"]: a for p in periodos.values() for a in p["anuncios"]}
    detalhes_anuncios(list(todos.values()))
    for p in periodos.values():
        for a in p["anuncios"]:
            a.update({k: todos[a["id"]][k] for k in ("status", "thumb", "instagram", "previa")})

    dados = {
        "nome": CFG["nome"],
        "subtitulo": CFG.get("subtitulo", ""),
        "conta": conta.get("name", ""),
        "moeda": conta.get("currency", "BRL"),
        "filtro": FILTRO,
        "gerado_em": agora.isoformat(timespec="minutes"),
        "gasto_minimo_criativo": CFG["criativos_gasto_minimo"],
        "periodos": periodos,
        "diario": serie_diaria(ontem),
    }
    soma_diaria = sum(d["gasto"] for d in dados["diario"])
    conferir(abs(soma_diaria - periodos["30"]["atual"]["gasto"]) < 0.05,
             "serie diaria nao bate com o total de 30 dias")

    json_dados = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    senha = os.environ.get(f"SENHA_VENDAS_{slug.upper().replace('-', '_')}", "")
    payload = criptografar(json_dados, senha) if senha else {"enc": False, "dados": dados}

    saida.mkdir(parents=True, exist_ok=True)
    html = (AQUI / "template_vendas.html").read_text(encoding="utf-8")
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    (saida / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(AQUI / "logo.png", saida / "logo.png")

    p30 = periodos["30"]["atual"]
    print(f"OK vendas/{slug}: 30d R$ {p30['gasto']:.2f}, {p30['compras']:.0f} compras, "
          f"{len(periodos['30']['campanhas'])} campanhas, {len(periodos['30']['anuncios'])} anuncios | "
          f"{'com senha' if senha else 'link aberto, sem senha'}")


def main():
    todos = sorted(p.parent.name for p in (AQUI / "vendas").glob("*/config.json"))
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
        '<title>C2Pro</title><body style="background:#05070A"></body>', encoding="utf-8")
    for slug in sys.argv[1:] or todos:
        gerar_cliente(slug)


if __name__ == "__main__":
    main()
