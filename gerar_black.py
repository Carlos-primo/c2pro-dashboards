"""Gera os dashboards da BLACK FRIDAY (Meta Ads), separados por conta de anuncio.

Cada pasta black/<slug>/ com um config.json vira site/black/<slug>/index.html, publicado em
https://carlos-primo.github.io/c2pro-dashboards/black/<slug>/

Reaproveita o gerador de vendas (gerar_vendas.py): mesmas metricas, mesmo funil, mesma consistencia.
Diferencas: o filtro e BLACK no nome da campanha (config "filtro"), o resultado principal e o LEAD,
e o dashboard junta varias contas de anuncio (config "contas"), com uma visao por conta e uma de todas.

config.json:
  {"nome": "...", "subtitulo": "...", "filtro": "BLACK", "criativos_gasto_minimo": 10,
   "contas": [{"id": "act_...", "nome": "Camila"}, {"id": "act_...", "nome": "Revenda"}],
   "google": {"customer_id": "7321202082", "nome": "Camila"}}     (opcional: aba Google Ads, mesmo filtro)

Uso local:   python gerar_black.py            (todos)
             python gerar_black.py wescley     (so um)
"""

import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import gerar_vendas as gv
from gerar import api, criptografar

AQUI = Path(__file__).resolve().parent
SITE = AQUI / "site" / "black"

EVENTOS_LEAD = ["lead", "offsite_conversion.fb_pixel_lead", "onsite_conversion.lead_grouped"]
SOMAVEIS = ["gasto", "impressoes", "cliques", "visitas", "checkouts", "compras", "faturamento", "leads"]

_metricas_vendas = gv.metricas


def metricas(linha):
    m = _metricas_vendas(linha)
    acoes = {a["action_type"]: float(a["value"]) for a in linha.get("actions", [])}
    m["leads"] = gv.primeiro(acoes, EVENTOS_LEAD)
    return m


# O gerador de vendas chama metricas() pelo nome do modulo, entao o patch vale pra ele inteiro.
gv.metricas = metricas


def somar(itens):
    """Soma metricas aditivas. Alcance nao e aditivo entre contas (a mesma pessoa conta duas vezes): fica 0."""
    total = {k: sum(i[k] for i in itens) for k in SOMAVEIS}
    total["alcance"] = 0
    return total


def juntar_periodo(periodos_contas):
    """periodos_contas: lista de periodos (um por conta) -> periodo com tudo somado."""
    base = periodos_contas[0]
    return {
        **{k: base[k] for k in ("inicio", "fim", "ant_inicio", "ant_fim")},
        "atual": somar([p["atual"] for p in periodos_contas]),
        "anterior": somar([p["anterior"] for p in periodos_contas]),
        "campanhas": sorted((c for p in periodos_contas for c in p["campanhas"]), key=lambda c: -c["gasto"]),
        "anuncios": [a for p in periodos_contas for a in p["anuncios"]],
    }


# ---------- Google Ads (aba separada, so com "google" no config) ----------
def google(gcfg, filtro, ontem):
    """Campanhas do Google Ads com o filtro no nome: totais, campanhas, anuncios, termos e dia a dia.
    Se a credencial do Google falhar, devolve None e a aba some sem derrubar o dashboard da Meta."""
    import gerar_google as gg
    try:
        g = gg.Google(gcfg["customer_id"])
        return _google(g, gg, filtro, ontem) | {"nome": gcfg.get("nome", "Google Ads"), "conta": gcfg["customer_id"]}
    except SystemExit as erro:
        print(f"  aviso: Google Ads ficou de fora ({erro})")
        return None


def _google(g, gg, filtro, ontem):
    onde = f"campaign.name REGEXP_MATCH '(?i).*{filtro}.*'"  # so campanhas da Black, sem diferenciar maiuscula
    met = "metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions, metrics.conversions_value"
    chaves = ("custo", "imp", "cli", "conv", "valor")

    def m(x):
        x = x["metrics"]
        return {"custo": gg.reais(x), "imp": int(x.get("impressions", 0)), "cli": int(x.get("clicks", 0)),
                "conv": float(x.get("conversions", 0)), "valor": float(x.get("conversionsValue", 0))}

    def total(itens):
        return {k: sum(i[k] for i in itens) for k in chaves}

    def tipo(canal):
        return {"PERFORMANCE_MAX": "Performance Max", "SEARCH": "Pesquisa", "DISPLAY": "Display",
                "VIDEO": "YouTube", "DEMAND_GEN": "Demand Gen", "SHOPPING": "Shopping"}.get(canal, canal.title())

    def campanhas(ini, fim):
        return [{"nome": r["campaign"]["name"], "status": r["campaign"].get("status", ""),
                 "tipo": tipo(r["campaign"].get("advertisingChannelType", "")), **m(r)}
                for r in g.q(f"SELECT campaign.name, campaign.status, campaign.advertising_channel_type, {met} "
                             f"FROM campaign WHERE {gg.entre(ini, fim)} AND {onde} AND metrics.impressions > 0")]

    def demand_gen(ads, ini, fim):
        """Titulos, descricoes e video do YouTube dos anuncios de video Demand Gen. Extra: se a consulta falhar,
        os cards ficam so com o nome, sem derrubar a aba."""
        try:
            pecas = {r["adGroupAd"]["ad"]["id"]: r["adGroupAd"]["ad"].get("demandGenVideoResponsiveAd", {}) for r in g.q(
                "SELECT ad_group_ad.ad.id, ad_group_ad.ad.demand_gen_video_responsive_ad.headlines, "
                "ad_group_ad.ad.demand_gen_video_responsive_ad.long_headlines, "
                "ad_group_ad.ad.demand_gen_video_responsive_ad.descriptions, ad_group_ad.ad.demand_gen_video_responsive_ad.videos "
                f"FROM ad_group_ad WHERE {gg.entre(ini, fim)} AND {onde} AND metrics.impressions > 0 "
                "AND ad_group_ad.ad.type = 'DEMAND_GEN_VIDEO_RESPONSIVE_AD'")}
            videos = {r["asset"]["resourceName"]: r["asset"].get("youtubeVideoAsset", {}).get("youtubeVideoId", "") for r in g.q(
                "SELECT asset.resource_name, asset.youtube_video_asset.youtube_video_id FROM asset WHERE asset.type = 'YOUTUBE_VIDEO'")}
        except SystemExit as erro:
            print(f"  aviso: textos dos anuncios Demand Gen ficaram de fora ({erro})")
            return
        for a in ads:
            p = pecas.get(a.pop("id", None))
            if not p:
                continue
            a["titulos"] = [h["text"] for h in p.get("headlines", []) + p.get("longHeadlines", [])]
            a["descricoes"] = [d["text"] for d in p.get("descriptions", [])]
            ids = [videos.get(v.get("asset"), "") for v in p.get("videos", [])]
            a["video"] = next((f"https://www.youtube.com/watch?v={i}" for i in ids if i), "")

    def anuncios(ini, fim):
        ads = []
        for r in g.q("SELECT campaign.name, ad_group.name, ad_group_ad.status, ad_group_ad.ad.id, ad_group_ad.ad.name, "
                     "ad_group_ad.ad.type, ad_group_ad.ad.final_urls, ad_group_ad.ad.responsive_search_ad.headlines, "
                     "ad_group_ad.ad.responsive_search_ad.descriptions, ad_group_ad.ad.responsive_display_ad.headlines, "
                     "ad_group_ad.ad.responsive_display_ad.descriptions, ad_group_ad.policy_summary.approval_status, "
                     f"{met} FROM ad_group_ad WHERE {gg.entre(ini, fim)} AND {onde} AND metrics.impressions > 0"):
            ad = r["adGroupAd"]["ad"]
            peca = ad.get("responsiveSearchAd") or ad.get("responsiveDisplayAd") or {}
            titulos = [h["text"] for h in peca.get("headlines", [])]
            ads.append({"id": ad["id"], "nome": ad.get("name") or (titulos[0] if titulos else f"Anúncio {ad['id']}"),
                        "tipo": ad.get("type", "").replace("_", " ").title(), "titulos": titulos,
                        "descricoes": [d["text"] for d in peca.get("descriptions", [])],
                        "url": (ad.get("finalUrls") or [""])[0], "campanha": r["campaign"]["name"],
                        "grupo": r["adGroup"]["name"], "status": r["adGroupAd"].get("status", ""),
                        "aprovacao": r["adGroupAd"].get("policySummary", {}).get("approvalStatus", ""), **m(r)})
        demand_gen(ads, ini, fim)
        # Performance Max nao tem ad_group_ad: o "anuncio" dela e o grupo de recursos.
        for r in g.q(f"SELECT campaign.name, asset_group.name, asset_group.status, asset_group.final_urls, {met} "
                     f"FROM asset_group WHERE {gg.entre(ini, fim)} AND {onde} AND metrics.impressions > 0"):
            a = r["assetGroup"]
            ads.append({"nome": a["name"], "tipo": "Grupo de recursos (PMax)", "titulos": [], "descricoes": [],
                        "url": (a.get("finalUrls") or [""])[0], "campanha": r["campaign"]["name"], "grupo": "",
                        "status": a.get("status", ""), "aprovacao": "", **m(r)})
        return sorted(ads, key=lambda a: -a["custo"])

    periodos = {}
    for dias in gv.PERIODOS:
        ini = ontem - dt.timedelta(days=dias - 1)
        pfim = ini - dt.timedelta(days=1)
        pini = pfim - dt.timedelta(days=dias - 1)
        camps = campanhas(ini, ontem)
        acoes = {}
        for r in g.q("SELECT segments.conversion_action_name, metrics.conversions FROM campaign "
                     f"WHERE {gg.entre(ini, ontem)} AND {onde} AND metrics.conversions > 0"):
            n = r["segments"]["conversionActionName"]
            acoes[n] = acoes.get(n, 0) + float(r["metrics"]["conversions"])
        t = total(camps)
        gv.conferir(abs(sum(acoes.values()) - t["conv"]) < 0.01, f"Google {dias}d: conversoes por tipo nao fecham")
        tv = gg.termos(g.q("SELECT campaign.name, campaign.advertising_channel_type, campaign_search_term_view.search_term, "
                           "metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions "
                           f"FROM campaign_search_term_view WHERE {gg.entre(ini, ontem)} AND {onde}"))
        periodos[str(dias)] = {
            "inicio": ini.isoformat(), "fim": ontem.isoformat(), "ant_inicio": pini.isoformat(), "ant_fim": pfim.isoformat(),
            "atual": t, "anterior": total(campanhas(pini, pfim)), "acoes": acoes,
            "campanhas": sorted(camps, key=lambda c: -c["custo"]), "anuncios": anuncios(ini, ontem),
            "termos": sorted([v for v in tv if v["conv"] > 0], key=lambda v: (-v["conv"], v["custo"]))[:20]}

    ini30 = ontem - dt.timedelta(days=29)
    por_dia = {}
    for r in g.q(f"SELECT segments.date, {met} FROM campaign WHERE {gg.entre(ini30, ontem)} AND {onde}"):
        d = por_dia.setdefault(r["segments"]["date"], dict.fromkeys(chaves, 0))
        for k, v in m(r).items():
            d[k] += v
    datas = [(ini30 + dt.timedelta(days=n)).isoformat() for n in range(30)]
    diario = [{"data": d, **por_dia.get(d, dict.fromkeys(chaves, 0))} for d in datas]
    gv.conferir(abs(sum(d["custo"] for d in diario) - periodos["30"]["atual"]["custo"]) < 0.05,
                "Google: serie diaria nao bate com o total de 30 dias")
    return {"periodos": periodos, "diario": diario}


def gerar_cliente(slug):
    cfg = json.loads((AQUI / "black" / slug / "config.json").read_text(encoding="utf-8"))
    gv.FILTRO = cfg["filtro"].upper()
    gv.CFG.clear()
    gv.CFG.update(nome=cfg["nome"], criativos_gasto_minimo=cfg["criativos_gasto_minimo"])
    saida = SITE / slug

    visoes, moedas, agora = {}, set(), None
    for c in cfg["contas"]:
        gv.CFG["conta_anuncio"] = c["id"]
        conta = api(c["id"], fields="name,currency,timezone_name")
        moedas.add(conta.get("currency", "BRL"))
        fuso = ZoneInfo(conta.get("timezone_name") or "America/Sao_Paulo")
        agora = agora or dt.datetime.now(fuso)
        ontem = agora.date() - dt.timedelta(days=1)

        status_camp = {x["id"]: x["effective_status"] for x in api(
            f"{c['id']}/campaigns", fields="effective_status", limit=500)}
        periodos = {str(d): gv.periodo(d, ontem, status_camp, {}) for d in gv.PERIODOS}

        todos = {a["id"]: a for p in periodos.values() for a in p["anuncios"]}
        gv.detalhes_anuncios(list(todos.values()))
        for p in periodos.values():
            for a in p["anuncios"]:
                a.update({k: todos[a["id"]][k] for k in ("status", "thumb", "instagram", "previa")})
                a["conta"] = c["nome"]
            for camp in p["campanhas"]:
                camp["conta"] = c["nome"]

        diario = gv.serie_diaria(ontem)
        gv.conferir(abs(sum(d["gasto"] for d in diario) - periodos["30"]["atual"]["gasto"]) < 0.05,
                    f"serie diaria da conta {c['nome']} nao bate com o total de 30 dias")
        visoes[c["nome"]] = {"periodos": periodos, "diario": diario}

    gv.conferir(len(moedas) == 1, f"contas com moedas diferentes: {sorted(moedas)}")
    nomes = list(visoes)
    visoes_todas = {
        "periodos": {k: juntar_periodo([visoes[n]["periodos"][k] for n in nomes]) for k in visoes[nomes[0]]["periodos"]},
        "diario": [{"data": visoes[nomes[0]]["diario"][i]["data"], **somar([visoes[n]["diario"][i] for n in nomes])}
                   for i in range(30)],
    }
    gv.conferir(abs(sum(d["gasto"] for d in visoes_todas["diario"]) -
                    sum(visoes[n]["periodos"]["30"]["atual"]["gasto"] for n in nomes)) < 0.05,
                "serie diaria somada nao bate com a soma das contas")

    dados = {
        "nome": cfg["nome"],
        "subtitulo": cfg.get("subtitulo", ""),
        "moeda": moedas.pop(),
        "filtro": gv.FILTRO,
        "gerado_em": agora.isoformat(timespec="minutes"),
        "gasto_minimo_criativo": cfg["criativos_gasto_minimo"],
        "contas": nomes,
        "visoes": {"todas": visoes_todas, **visoes},
        "google": google(cfg["google"], gv.FILTRO, ontem) if cfg.get("google") else None,
    }

    senha = os.environ.get(f"SENHA_BLACK_{slug.upper().replace('-', '_')}", "")
    json_dados = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    payload = criptografar(json_dados, senha) if senha else {"enc": False, "dados": dados}

    saida.mkdir(parents=True, exist_ok=True)
    html = (AQUI / "template_black.html").read_text(encoding="utf-8")
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    (saida / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(AQUI / "logo.png", saida / "logo.png")

    partes = "; ".join(
        f"{n}: R$ {visoes[n]['periodos']['30']['atual']['gasto']:.2f}, {visoes[n]['periodos']['30']['atual']['leads']:.0f} leads, "
        f"{len(visoes[n]['periodos']['30']['campanhas'])} campanhas" for n in nomes)
    if dados["google"]:
        gt = dados["google"]["periodos"]["30"]
        partes += (f"; Google: R$ {gt['atual']['custo']:.2f}, {gt['atual']['conv']:.0f} conversoes, "
                   f"{len(gt['campanhas'])} campanhas, {len(gt['anuncios'])} anuncios")
    print(f"OK black/{slug}: 30d {partes} | {'com senha' if senha else 'link aberto, sem senha'}")


def main():
    todos = sorted(p.parent.name for p in (AQUI / "black").glob("*/config.json"))
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
        '<title>C2Pro</title><body style="background:#05070A"></body>', encoding="utf-8")
    falhas = []
    for slug in sys.argv[1:] or todos:
        try:
            gerar_cliente(slug)
        except SystemExit as erro:
            falhas.append(slug)
            print(f"FALHOU black/{slug}: {erro}", file=sys.stderr)
    if falhas:
        sys.exit(f"Dashboards com erro: {', '.join(falhas)}")


if __name__ == "__main__":
    main()
