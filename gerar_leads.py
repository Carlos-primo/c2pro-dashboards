"""Gera os dashboards de LEADS (Meta Ads) direto da API da Meta, sem Claude no meio.

Cada pasta leads/<slug>/ com um config.json vira um dashboard em site/leads/<slug>/index.html,
publicado em https://carlos-primo.github.io/c2pro-dashboards/leads/<slug>/

Uso local:   python gerar_leads.py            (todos os clientes de leads)
             python gerar_leads.py atento     (so um)

Mostra so volume: criativos que mais geraram leads e o funil geral de todas as campanhas da conta
(impressoes > cliques no link > visitas a pagina, quando houver > leads).
Regra: investimento, custo e comparacao com periodo anterior NAO entram na pagina nem no payload. O gasto so e usado aqui
dentro pra conferir a consistencia dos totais.

Variaveis de ambiente: as mesmas do gerar.py (META_ADS_TOKEN, SENHA_LEADS_<SLUG> opcional).
"""

import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from gerar import api, criptografar

AQUI = Path(__file__).resolve().parent
SITE = AQUI / "site" / "leads"
CFG = {}

PERIODOS = [7, 30]

CAMPOS_METRICAS = "spend,impressions,reach,inline_link_clicks,actions"

# A Meta devolve o mesmo evento com nomes diferentes conforme a origem; usa o primeiro que existir.
# "lead" ja soma formulario instantaneo e pixel; os outros sao fallback.
EVENTOS = {
    "visitas": ["landing_page_view", "omni_landing_page_view"],
    "leads": ["lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead"],
}


def conferir(condicao, mensagem):
    """Aborta a geracao em vez de publicar um dashboard com dado inconsistente."""
    if not condicao:
        sys.exit(f"ERRO de consistencia ({CFG.get('nome')}): {mensagem}")


def insights(nivel, inicio, fim, campos, **extra):
    return api(
        f"{CFG['conta_anuncio']}/insights",
        level=nivel,
        fields=campos,
        time_range=json.dumps({"since": inicio.isoformat(), "until": fim.isoformat()}),
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
    return {
        "impressoes": int(linha.get("impressions", 0)),
        "alcance": int(linha.get("reach", 0)),
        "cliques": float(linha.get("inline_link_clicks", 0)),
        "visitas": primeiro(acoes, EVENTOS["visitas"]),
        "leads": primeiro(acoes, EVENTOS["leads"]),
    }


def conta_total(inicio, fim):
    linhas = insights("account", inicio, fim, CAMPOS_METRICAS)
    if not linhas:
        return metricas({}), 0.0
    return metricas(linhas[0]), float(linhas[0].get("spend", 0))


def detalhes_anuncios(anuncios):
    """Status, miniatura e links do criativo (Instagram e previa), 50 por chamada."""
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


def agrupar_criativos(anuncios):
    """O mesmo criativo roda em varios conjuntos com o mesmo nome de anuncio: soma por nome.
    Links e miniatura vem do anuncio que mais gerou leads (desempate por impressoes)."""
    grupos = {}
    for a in anuncios:
        grupos.setdefault(a["nome"], []).append(a)
    criativos = []
    for nome, lista in grupos.items():
        melhor = max(lista, key=lambda a: (a["leads"], a["impressoes"]))
        criativos.append({
            "nome": nome,
            "leads": sum(a["leads"] for a in lista),
            "impressoes": sum(a["impressoes"] for a in lista),
            "cliques": sum(a["cliques"] for a in lista),
            "conjuntos": len({a["conjunto"] for a in lista}),
            "ativo": any(a.get("status") == "ACTIVE" for a in lista),
            **{k: melhor.get(k, "") for k in ("thumb", "instagram", "previa")},
        })
    criativos.sort(key=lambda c: (-c["leads"], -c["impressoes"]))
    return criativos


def periodo(dias, ontem):
    fim = ontem
    inicio = fim - dt.timedelta(days=dias - 1)

    campanhas, gasto_campanhas = [], 0.0
    for l in insights("campaign", inicio, fim, "campaign_name," + CAMPOS_METRICAS):
        gasto = float(l.get("spend", 0))
        if gasto > 0:
            gasto_campanhas += gasto
            campanhas.append({"nome": l["campaign_name"], "leads": metricas(l)["leads"]})
    campanhas.sort(key=lambda c: -c["leads"])

    anuncios = []
    for l in insights("ad", inicio, fim, "ad_id,ad_name,adset_id," + CAMPOS_METRICAS):
        if float(l.get("spend", 0)) > 0:
            anuncios.append({"id": l["ad_id"], "nome": l["ad_name"], "conjunto": l["adset_id"], **metricas(l)})

    atual, gasto_conta = conta_total(inicio, fim)
    conferir(abs(gasto_campanhas - gasto_conta) < 0.05,
             f"total {dias}d da conta difere da soma das campanhas")
    soma_leads = sum(a["leads"] for a in anuncios)
    conferir(abs(soma_leads - atual["leads"]) < 0.5,
             f"leads {dias}d da conta ({atual['leads']:.0f}) diferem da soma dos anuncios ({soma_leads:.0f})")

    return {
        "inicio": inicio.isoformat(), "fim": fim.isoformat(),
        "atual": atual,
        "campanhas": campanhas,
        "anuncios": anuncios,
    }


def gerar_cliente(slug):
    CFG.clear()
    CFG.update(json.loads((AQUI / "leads" / slug / "config.json").read_text(encoding="utf-8")))
    saida = SITE / slug

    conta = api(CFG["conta_anuncio"], fields="name,timezone_name")
    fuso = ZoneInfo(conta.get("timezone_name") or "America/Sao_Paulo")
    agora = dt.datetime.now(fuso)
    ontem = agora.date() - dt.timedelta(days=1)

    periodos = {str(d): periodo(d, ontem) for d in PERIODOS}

    # Detalhes dos criativos uma vez so, pra todos os anuncios de todos os periodos.
    todos = {a["id"]: a for p in periodos.values() for a in p["anuncios"]}
    detalhes_anuncios(list(todos.values()))
    for p in periodos.values():
        for a in p["anuncios"]:
            a.update({k: todos[a["id"]][k] for k in ("status", "thumb", "instagram", "previa")})
        p["criativos"] = agrupar_criativos(p.pop("anuncios"))

    dados = {
        "nome": CFG["nome"],
        "subtitulo": CFG.get("subtitulo", ""),
        "gerado_em": agora.isoformat(timespec="minutes"),
        "periodos": periodos,
    }

    json_dados = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    senha = os.environ.get(f"SENHA_LEADS_{slug.upper().replace('-', '_')}", "")
    payload = criptografar(json_dados, senha) if senha else {"enc": False, "dados": dados}

    saida.mkdir(parents=True, exist_ok=True)
    html = (AQUI / "template_leads.html").read_text(encoding="utf-8")
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    (saida / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(AQUI / "logo.png", saida / "logo.png")

    p30 = periodos["30"]
    com_lead = sum(1 for c in p30["criativos"] if c["leads"] > 0)
    print(f"OK leads/{slug}: 30d {p30['atual']['leads']:.0f} leads, {len(p30['campanhas'])} campanhas, "
          f"{com_lead} de {len(p30['criativos'])} criativos com lead | "
          f"{'com senha' if senha else 'link aberto, sem senha'}")


def main():
    todos = sorted(p.parent.name for p in (AQUI / "leads").glob("*/config.json"))
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
        '<title>C2Pro</title><body style="background:#05070A"></body>', encoding="utf-8")
    for slug in sys.argv[1:] or todos:
        gerar_cliente(slug)


if __name__ == "__main__":
    main()
