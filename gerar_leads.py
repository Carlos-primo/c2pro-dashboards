"""Gera os dashboards de LEADS (Meta Ads) direto da API da Meta, sem Claude no meio.

Cada pasta leads/<slug>/ com um config.json vira um dashboard em site/leads/<slug>/index.html,
publicado em https://carlos-primo.github.io/c2pro-dashboards/leads/<slug>/

Uso local:   python gerar_leads.py            (todos os clientes de leads)
             python gerar_leads.py atento     (so um)

Mostra so volume: criativos que mais geraram leads e o funil geral de todas as campanhas da conta
(impressoes > cliques no link > visitas a pagina, quando houver > leads).
Regra: investimento, custo e comparacao com periodo anterior NAO entram na pagina nem no payload. O gasto so e usado aqui
dentro pra conferir a consistencia dos totais.

Respostas do formulario (opcional): com o campo "planilha" no config.json, le a planilha publica do Google
Sheets que recebe os leads do formulario e soma os qualificados, por segmento e por anuncio. So numeros agregados entram
no payload; nome e telefone nunca saem daqui.

Vendas da Hotmart (opcional): com o campo "hotmart" no config.json, compara as vendas reais do produto com as compras
que o pixel da Meta registrou, por periodo e acumulado desde o inicio das vendas. Ver gerar_hotmart.py.

Variaveis de ambiente: as mesmas do gerar.py (META_ADS_TOKEN, SENHA_LEADS_<SLUG> opcional) e, com Hotmart,
HOTMART_<SLUG>_CLIENT_ID e HOTMART_<SLUG>_CLIENT_SECRET.
"""

import csv
import datetime as dt
import io
import json
import os
import shutil
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from gerar import api, criptografar
import gerar_google
import gerar_hotmart

AQUI = Path(__file__).resolve().parent
SITE = AQUI / "site" / "leads"
CFG = {}

PERIODOS = [7, 30]

CAMPOS_METRICAS = ("spend,impressions,reach,inline_link_clicks,actions,action_values,"
                   "video_p25_watched_actions,video_p50_watched_actions,video_p75_watched_actions,"
                   "video_p100_watched_actions,video_thruplay_watched_actions,video_avg_time_watched_actions")

# Engajamento do post e do video. Somam por criativo; taxas (hook rate, retencao) saem da soma no navegador.
CAMPOS_SOMA = ("leads", "contatos", "views3s", "p25", "p50", "p75", "p100", "thruplay", "engajamento",
               "reacoes", "comentarios", "compartilhamentos", "salvamentos")

# A Meta devolve o mesmo evento com nomes diferentes conforme a origem; usa o primeiro que existir.
# "lead" ja soma formulario instantaneo e pixel; os outros sao fallback.
EVENTOS = {
    "visitas": ["landing_page_view", "omni_landing_page_view"],
    "leads": ["lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead"],
    # Contatos = conversas iniciadas por mensagem (WhatsApp, Direct, Messenger), o resultado das campanhas de mensagem.
    "contatos": ["onsite_conversion.messaging_conversation_started_7d"],
    # Compras do pixel, pra comparar com a Hotmart.
    "compras": ["purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"],
}


def conferir(condicao, mensagem):
    """Aborta a geracao em vez de publicar um dashboard com dado inconsistente."""
    if not condicao:
        sys.exit(f"ERRO de consistencia ({CFG.get('nome')}): {mensagem}")


def insights(nivel, inicio, fim, campos, **extra):
    # "filtro_campanha" (so o gerar_contatos_distribuicao.py usa): so campanhas com esse trecho no nome.
    if CFG.get("filtro_campanha"):
        extra["filtering"] = json.dumps([{"field": "campaign.name", "operator": "CONTAIN",
                                          "value": CFG["filtro_campanha"]}])
    return api(
        f"{CFG['conta_anuncio']}/insights",
        level=nivel,
        fields=campos,
        time_range=json.dumps({"since": inicio.isoformat(), "until": fim.isoformat()}),
        limit=500,
        **extra,
    )


def PRINCIPAL():
    """Metrica que ranqueia os criativos: 'leads' (padrao) ou 'contatos', definida no config.json."""
    return CFG.get("metrica_principal", "leads")


def primeiro(dic, chaves):
    for k in chaves:
        if k in dic:
            return dic[k]
    return 0.0


def metricas(linha):
    acoes = {a["action_type"]: float(a["value"]) for a in linha.get("actions", [])}
    valores = {a["action_type"]: float(a["value"]) for a in linha.get("action_values", [])}
    video = lambda campo: sum(float(a["value"]) for a in linha.get(campo, []))
    return {
        "impressoes": int(linha.get("impressions", 0)),
        "alcance": int(linha.get("reach", 0)),
        "cliques": float(linha.get("inline_link_clicks", 0)),
        "visitas": primeiro(acoes, EVENTOS["visitas"]),
        "leads": primeiro(acoes, EVENTOS["leads"]),
        "contatos": primeiro(acoes, EVENTOS["contatos"]),
        "compras": primeiro(acoes, EVENTOS["compras"]),
        "valor_compras": primeiro(valores, EVENTOS["compras"]),
        "views3s": acoes.get("video_view", 0),
        "p25": video("video_p25_watched_actions"),
        "p50": video("video_p50_watched_actions"),
        "p75": video("video_p75_watched_actions"),
        "p100": video("video_p100_watched_actions"),
        "thruplay": video("video_thruplay_watched_actions"),
        "tempo_medio": video("video_avg_time_watched_actions"),
        "engajamento": acoes.get("post_engagement", 0),
        "reacoes": acoes.get("post_reaction", 0),
        "comentarios": acoes.get("comment", 0),
        "compartilhamentos": acoes.get("post", 0),
        "salvamentos": acoes.get("onsite_conversion.post_save", 0),
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
        melhor = max(lista, key=lambda a: (a[PRINCIPAL()], a["impressoes"]))
        criativos.append({
            "nome": nome,
            "impressoes": sum(a["impressoes"] for a in lista),
            "cliques": sum(a["cliques"] for a in lista),
            "alcance": sum(a["alcance"] for a in lista),
            **{k: sum(a[k] for a in lista) for k in CAMPOS_SOMA},
            "conjuntos": len({a["conjunto"] for a in lista}),
            "ativo": any(a.get("status") == "ACTIVE" for a in lista),
            **{k: melhor.get(k, "") for k in ("thumb", "instagram", "previa")},
        })
    criativos.sort(key=lambda c: (-c[PRINCIPAL()], -c["impressoes"]))
    return criativos


def periodo(dias, ontem):
    fim = ontem
    inicio = fim - dt.timedelta(days=dias - 1)

    campanhas, gasto_campanhas = [], 0.0
    for l in insights("campaign", inicio, fim, "campaign_name," + CAMPOS_METRICAS):
        gasto = float(l.get("spend", 0))
        conferir(CFG.get("filtro_campanha", "").upper() in l["campaign_name"].upper(),
                 f"campanha fora do filtro: {l['campaign_name']}")
        if gasto > 0:
            gasto_campanhas += gasto
            m = metricas(l)
            campanhas.append({"nome": l["campaign_name"], "leads": m["leads"], "contatos": m["contatos"],
                              "compras": m["compras"]})
    campanhas.sort(key=lambda c: -c[PRINCIPAL()])

    anuncios = []
    for l in insights("ad", inicio, fim, "ad_id,ad_name,adset_id," + CAMPOS_METRICAS):
        if float(l.get("spend", 0)) > 0:
            anuncios.append({"id": l["ad_id"], "nome": l["ad_name"], "conjunto": l["adset_id"], **metricas(l)})

    atual, gasto_conta = conta_total(inicio, fim)
    conferir(abs(gasto_campanhas - gasto_conta) < 0.05,
             f"total {dias}d da conta difere da soma das campanhas")
    for k in ("leads", "contatos"):
        soma = sum(a[k] for a in anuncios)
        conferir(abs(soma - atual[k]) < 0.5,
                 f"{k} {dias}d da conta ({atual[k]:.0f}) diferem da soma dos anuncios ({soma:.0f})")

    return {
        "inicio": inicio.isoformat(), "fim": fim.isoformat(),
        "atual": atual,
        "campanhas": campanhas,
        "anuncios": anuncios,
    }


def bloco_google(cid, ontem):
    """Contatos do Google Ads (config "google_customer_id") pra juntar ao dashboard da Meta.
    Mesma regra da pagina: sem investimento, custo ou comparacao. Reaproveita as consultas do gerar_google.py."""
    g = gerar_google.Google(cid)
    saida = {}
    for d in PERIODOS:
        p = gerar_google.periodo(g, d, ontem)
        saida[str(d)] = {
            "atual": {"contatos": p["cur"]["conv"], "impressoes": p["cur"]["imp"], "cliques": p["cur"]["cli"]},
            "campanhas": [{"nome": c["nome"], "tipo": c["tipo"], "contatos": c["conv"], "impressoes": c["imp"],
                           "cliques": c["cli"]} for c in sorted(p["camps"], key=lambda c: -c["conv"])],
            "termos": [{"t": v["t"], "contatos": v["conv"], "cliques": v["cli"], "origem": v["camp"]}
                       for v in p["conv"][:15]],
            "temas": [{"t": v["t"], "contatos": v["conv"], "cliques": v["cli"]}
                      for v in p["cats"] if v["conv"] > 0][:10],
        }
    return saida


def bloco_hotmart(slug, periodos, ontem, fuso):
    """Vendas da Hotmart x compras do pixel, em cada periodo e no acumulado desde cfg["desde"].
    O pixel conta compras de todas as campanhas da conta; a Hotmart conta todas as vendas do produto, de qualquer origem."""
    # Sem credencial o dashboard sai sem o bloco, em vez de ficar fora do ar. Credencial errada e erro.
    if not gerar_hotmart.credencial(slug):
        print(f"::warning::leads/{slug} saiu sem a Hotmart: faltam os secrets HOTMART_{slug.upper().replace('-', '_')}_*")
        return None
    cfg = CFG["hotmart"]
    desde = dt.date.fromisoformat(cfg["desde"])
    inicio = min([desde] + [dt.date.fromisoformat(p["inicio"]) for p in periodos.values()])
    hm = gerar_hotmart.Hotmart(slug)
    linhas = gerar_hotmart.vendas(hm, list(cfg["produtos"]), inicio, ontem, fuso)
    for p in periodos.values():
        p["hotmart"] = gerar_hotmart.resumo(linhas, dt.date.fromisoformat(p["inicio"]), dt.date.fromisoformat(p["fim"]))

    camps = [{"nome": l["campaign_name"], **{k: metricas(l)[k] for k in ("compras", "valor_compras")}}
             for l in insights("campaign", desde, ontem, "campaign_name,actions,action_values")]
    camps = sorted((c for c in camps if c["compras"] > 0), key=lambda c: -c["compras"])
    total, _ = conta_total(desde, ontem)
    conferir(abs(sum(c["compras"] for c in camps) - total["compras"]) < 0.5,
             "compras do pixel no acumulado diferem da soma das campanhas")
    return {"produtos": list(cfg["produtos"].values()), "inicio": desde.isoformat(), "fim": ontem.isoformat(),
            **gerar_hotmart.resumo(linhas, desde, ontem),
            "pixel": {"compras": total["compras"], "valor": round(total["valor_compras"], 2), "campanhas": camps}}


def ler_planilha(cfg):
    """Linhas da aba indicada no config ("planilha": {"id", "gid", "segmento", "qualificacao", "qualificado"}).
    "segmento" e "qualificacao" sao trechos do nome da coluna; "qualificado" e a resposta que qualifica o lead.
    A planilha precisa estar como "qualquer pessoa com o link pode ver"."""
    url = f"https://docs.google.com/spreadsheets/d/{cfg['id']}/export?format=csv&gid={cfg['gid']}"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    if "text/csv" not in resp.headers.get("content-type", ""):
        sys.exit(f"ERRO planilha ({CFG.get('nome')}): a planilha nao esta publica para quem tem o link")
    linhas = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))
    if not linhas:
        return []
    coluna = lambda trecho: next((c for c in linhas[0] if trecho in c.lower()), None)
    col_seg, col_qual = coluna(cfg["segmento"]), coluna(cfg["qualificacao"])
    conferir(col_seg and col_qual, "colunas de segmento e qualificacao nao encontradas na planilha")
    return [{"criado": dt.datetime.fromisoformat(l["created_time"]), "anuncio": l.get("ad_name", ""),
             "segmento": l[col_seg].strip().lower(),
             "qualificado": l[col_qual].strip().lower() == cfg["qualificado"]} for l in linhas]


def resumo_formulario(linhas, inicio, fim, fuso):
    """Respostas do periodo, somadas por segmento e por anuncio. Datas no fuso da conta, como a Meta."""
    doper = [l for l in linhas if inicio <= l["criado"].astimezone(fuso).date() <= fim]
    qual = lambda lista: sum(1 for l in lista if l["qualificado"])
    agrupar = lambda chave: {k: [l for l in doper if l[chave] == k] for k in {l[chave] for l in doper}}
    return {
        "total": len(doper), "qualificados": qual(doper),
        "segmentos": sorted(({"nome": k.replace("_", " ").capitalize(), "leads": len(v), "qualificados": qual(v)}
                             for k, v in agrupar("segmento").items()), key=lambda s: -s["leads"]),
        "anuncios": {k: {"leads": len(v), "qualificados": qual(v)} for k, v in agrupar("anuncio").items()},
    }


def montar_dados(slug):
    """Dados do dashboard do cliente em CFG (o gerar_contatos_distribuicao.py tambem usa)."""
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

    if CFG.get("planilha"):
        linhas = ler_planilha(CFG["planilha"])
        for p in periodos.values():
            p["formulario"] = resumo_formulario(linhas, dt.date.fromisoformat(p["inicio"]),
                                                dt.date.fromisoformat(p["fim"]), fuso)

    if CFG.get("google_customer_id"):
        for k, g in bloco_google(CFG["google_customer_id"], ontem).items():
            periodos[k]["google"] = g

    hotmart = bloco_hotmart(slug, periodos, ontem, fuso) if CFG.get("hotmart") else None

    return {
        "nome": CFG["nome"],
        "subtitulo": CFG.get("subtitulo", ""),
        "principal": PRINCIPAL(),
        "gerado_em": agora.isoformat(timespec="minutes"),
        "periodos": periodos,
        "hotmart": hotmart,
    }


def gerar_cliente(slug):
    CFG.clear()
    CFG.update(json.loads((AQUI / "leads" / slug / "config.json").read_text(encoding="utf-8")))
    saida = SITE / slug
    dados = montar_dados(slug)
    periodos, hotmart = dados["periodos"], dados["hotmart"]

    json_dados = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    senha = os.environ.get(f"SENHA_LEADS_{slug.upper().replace('-', '_')}", "")
    payload = criptografar(json_dados, senha) if senha else {"enc": False, "dados": dados}

    saida.mkdir(parents=True, exist_ok=True)
    html = (AQUI / "template_leads.html").read_text(encoding="utf-8")
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    (saida / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(AQUI / "logo.png", saida / "logo.png")

    p30 = periodos["30"]
    if "formulario" in p30:
        f = p30["formulario"]
        print(f"   + Planilha: 30d {f['total']} respostas, {f['qualificados']} qualificados")
    if hotmart:
        print(f"   + Hotmart: desde {hotmart['inicio']} {hotmart['vendas']} vendas "
              f"x {hotmart['pixel']['compras']:.0f} compras no pixel; 30d {p30['hotmart']['vendas']} vendas")
    if "google" in p30:
        print(f"   + Google Ads: 30d {p30['google']['atual']['contatos']:.0f} contatos")
    com_lead = sum(1 for c in p30["criativos"] if c[PRINCIPAL()] > 0)
    print(f"OK leads/{slug}: 30d {p30['atual'][PRINCIPAL()]:.0f} {PRINCIPAL()}, {len(p30['campanhas'])} campanhas, "
          f"{com_lead} de {len(p30['criativos'])} criativos com lead | "
          f"{'com senha' if senha else 'link aberto, sem senha'}")


def main():
    todos = sorted(p.parent.name for p in (AQUI / "leads").glob("*/config.json"))
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
