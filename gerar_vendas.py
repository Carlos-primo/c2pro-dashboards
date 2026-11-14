"""Gera os dashboards de VENDAS (Meta Ads) direto da API da Meta, sem Claude no meio.

Cada pasta vendas/<slug>/ com um config.json vira um dashboard em site/vendas/<slug>/index.html,
publicado em https://carlos-primo.github.io/c2pro-dashboards/vendas/<slug>/

Uso local:   python gerar_vendas.py            (todos os clientes de vendas)
             python gerar_vendas.py ordus      (so um)

Funil: impressoes (CPM) > cliques no link (CTR) > visitas a pagina (connect rate)
       > checkouts (pagina > checkout) > compras (checkout > compra)

Opcionais no config.json, so quando o Carlos pedir para aquele cliente:
  "filtros_extra": ["LOWTICKET"]  aceita tambem campanhas com esse trecho no nome (excecao a regra do VENDAS)
  "desde": "2026-09-18"           periodo a mais, do dia indicado ate ontem
  "hotmart": {"produtos": {"<id>": "<nome>"}}  vendas reais da Hotmart x compras do pixel (ver gerar_hotmart.py)
  "excluir": ["BLACK"]            tira campanhas com esse trecho no nome, mesmo tendo VENDAS (Vita Hair: a Black
                                  da Debora tem dashboard proprio)
  "so_dados": true                dashboard so com numeros: sem alerta, sugestao nem textos explicativos

Visao completa de trafego (BJJ Protech, 13/11/2026). Tudo opcional e so com pedido do Carlos; sempre dentro do
filtro VENDAS (as frentes so separam o que ja passou nele):
  "mes_atual": true               periodo "Este mes", do dia 1 ate ontem (abre nele)
  "frentes": [{"nome": "Black: aluno", "contem": ["BLACK26"]}, ...]
                                  agrupa campanha + conjunto pelo nome (sem acento, maiuscula; a primeira regra que
                                  casar ganha, o resto vira "Outras") e mostra investimento, compras, faturamento e
                                  ROAS de cada frente
  "destinos": [{"nome": "Landing page", "prefixo": "lp.site.com"}, {"nome": "Catalogo", "campanha": "CATALOGO"}]
                                  vendas do pixel por pagina de destino do anuncio (LP x site). Sem regra: o endereco
  "atletas": {"campanha": "INSTRUCIONAIS", "dias": 90, "repasse": 0.5}
                                  campanha de cursos de coproducao separada por atleta (pelo endereco do curso,
                                  ".../<curso>-com-<atleta>", ou pelo conjunto), com a sobra depois do repasse
  "google": {"customer_id": "...", "acao_compra": "purchase"}
                                  secao Google Ads separada (nao soma com a Meta), so campanhas com VENDAS no nome,
                                  compras so da acao de conversao que tem "acao_compra" no nome
  "regua_roas": 6                 ROAS minimo combinado com o cliente (Meta + Google), mostrado contra o periodo
  "faturamento_real": {"fonte": "Asaas"}
                                  quadro faturamento real x plataformas; sem integracao ainda, mostra "aguardando"
  "senha_obrigatoria": true       nao gera sem o secret SENHA_VENDAS_<SLUG> (previa local: PREVIA_SEM_SENHA=1)
  "paginas": [{"nome": "Black", "contem": ["BLACK26"]}, ...]
                                  uma aba por tipo de campanha (pelo nome da campanha; a primeira regra que casar ganha),
                                  com resumo, funil, dia a dia, campanhas e criativos so daquele tipo. A aba Geral fica
                                  como sempre
  "distribuicao": true            aba Distribuicao com o dashboard de distribuicao (campanhas com DISTRIBUICAO no nome,
                                  gerar.py), dentro do mesmo payload (mesma senha)

Variaveis de ambiente: as mesmas do gerar.py (META_ADS_TOKEN, SENHA_VENDAS_<SLUG> opcional) e, com Hotmart,
HOTMART_<SLUG>_CLIENT_ID e HOTMART_<SLUG>_CLIENT_SECRET.
"""

import datetime as dt
import json
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

from gerar import api, criptografar, diario_seguro
import gerar_hotmart

AQUI = Path(__file__).resolve().parent
SITE = AQUI / "site" / "vendas"
CFG = {}

# Regra da agencia: dashboard de vendas so mostra campanhas com VENDAS no nome.
# Fixo no codigo de proposito. Excecao so por pedido do Carlos, no config do cliente ("filtros_extra"):
# Rocha Mazzo (25/10/2026), cujas campanhas de venda do low ticket se chamam LOWTICKET.
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


def filtros():
    return [FILTRO] + [sem_acento(f) for f in CFG.get("filtros_extra", [])]


def excluidos():
    return [sem_acento(f) for f in CFG.get("excluir", [])]


def eh_vendas(nome_campanha):
    nome = sem_acento(nome_campanha)
    return any(f in nome for f in filtros()) and not any(e in nome for e in excluidos())


def temperatura(nome_campanha):
    """quente (token Q), frio (token ASP ou F) ou "" se o nome nao diz. Ex: ..._MAP_DO_FIO_Q_OUT_26 -> quente."""
    tokens = set(re.split(r"[^A-Z0-9]+", sem_acento(nome_campanha)))
    quente = "Q" in tokens
    frio = bool(tokens & {"ASP", "F"})
    return "quente" if quente and not frio else "frio" if frio and not quente else ""


def conferir(condicao, mensagem):
    """Aborta a geracao em vez de publicar um dashboard com dado fora da regra."""
    if not condicao:
        sys.exit(f"ERRO de consistencia ({CFG.get('nome')}): {mensagem}")


def insights(nivel, inicio, fim, campos, ids=None, **extra):
    """ids: restringe a essas campanhas (segmento quente/frio), sempre dentro do filtro VENDAS.
    Com filtros_extra a Meta nao aceita "contem A ou B" no nome: filtra pelos IDs das campanhas que passam no filtro."""
    if CFG.get("filtros_extra"):
        ids = [i for i in CFG["_ids_filtro"] if ids is None or i in ids] or ["0"]
        filtro = []
    else:
        filtro = [{"field": "campaign.name", "operator": "CONTAIN", "value": FILTRO}]
        filtro += [{"field": "campaign.name", "operator": "NOT_CONTAIN", "value": e} for e in excluidos()]
    if ids is not None:
        filtro.append({"field": "campaign.id", "operator": "IN", "value": ids})
    return api(
        f"{CFG['conta_anuncio']}/insights",
        level=nivel,
        fields=campos,
        time_range=json.dumps({"since": inicio.isoformat(), "until": fim.isoformat()}),
        filtering=json.dumps(filtro),
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


def conta_total(inicio, fim, ids=None):
    linhas = insights("account", inicio, fim, CAMPOS_METRICAS, ids=ids)
    return metricas(linhas[0]) if linhas else metricas({})


def precisa_url():
    return bool(CFG.get("destinos") or CFG.get("atletas"))


def url_destino(criativo):
    """Primeiro link de destino do criativo (link simples, video, carrossel ou Advantage+), sem os da propria Meta."""
    txt = json.dumps({k: criativo.get(k) for k in ("object_story_spec", "asset_feed_spec", "link_url")})
    for u in re.findall(r'https?://[^"\\\s]+', txt):
        if not re.search(r"facebook\.com|fbcdn|instagram\.com|fb\.me", u):
            return u
    return ""


def detalhes_anuncios(anuncios):
    """Adiciona status, miniatura e links do criativo (Instagram e previa), 50 por chamada.
    Com "destinos" ou "atletas" no config, tambem o link de destino do anuncio."""
    extra = ",object_story_spec,asset_feed_spec,link_url" if precisa_url() else ""
    for i in range(0, len(anuncios), 50):
        lote = anuncios[i:i + 50]
        filtro = [{"field": "ad.id", "operator": "IN", "value": [a["id"] for a in lote]}]
        resp = {ad["id"]: ad for ad in api(
            f"{CFG['conta_anuncio']}/ads",
            fields="effective_status,preview_shareable_link,"
                   "creative{thumbnail_url,instagram_permalink_url" + extra + "}",
            filtering=json.dumps(filtro), limit=50)}
        for a in lote:
            info = resp.get(a["id"], {})
            criativo = info.get("creative", {})
            a["status"] = info.get("effective_status", "")
            a["thumb"] = criativo.get("thumbnail_url", "")
            a["instagram"] = criativo.get("instagram_permalink_url", "")
            a["previa"] = info.get("preview_shareable_link", "")
            if extra:
                a["url"] = url_destino(criativo)


# ---------- visao completa de trafego (opcional, ver o topo do arquivo) ----------
SOMAVEIS = ("gasto", "impressoes", "cliques", "visitas", "checkouts", "compras", "faturamento")


def agrupar(itens, chave):
    grupos = {}
    for a in itens:
        g = grupos.setdefault(chave(a), {"nome": chave(a), "anuncios": 0, **dict.fromkeys(SOMAVEIS, 0.0)})
        g["anuncios"] += 1
        for k in SOMAVEIS:
            g[k] += a.get(k, 0)
    return sorted(grupos.values(), key=lambda g: -g["gasto"])


def pagina_de(nome_campanha):
    nome = sem_acento(nome_campanha)
    for p in CFG["paginas"]:
        if all(sem_acento(t) in nome for t in p["contem"]):
            return p["nome"]
    return "Outras"


def somar(itens):
    """Soma as metricas de varias campanhas. Alcance vira soma (aproximado: a mesma pessoa conta em cada campanha)."""
    tot = metricas({})
    for i in itens:
        for k in tot:
            tot[k] += i.get(k, 0)
    return tot


def nomes_paginas():
    return [p["nome"] for p in CFG["paginas"]] + ["Outras"]


def diario_paginas(ontem):
    """Dia a dia de cada aba numa chamada so (nivel de campanha, 30 dias), somado pela aba da campanha."""
    inicio = ontem - dt.timedelta(days=29)
    linhas = diario_seguro(lambda c, i, f, **e: insights("campaign", i, f, c, **e),
                           "campaign_name," + CAMPOS_METRICAS, inicio, ontem)
    por = {n: {} for n in nomes_paginas()}
    for l in linhas:
        dia = por[pagina_de(l["campaign_name"])].setdefault(l["date_start"], metricas({}))
        for k, v in metricas(l).items():
            dia[k] += v
    datas = [(inicio + dt.timedelta(days=n)).isoformat() for n in range(30)]
    return {n: [{"data": d, **por[n].get(d, metricas({}))} for d in datas] for n in nomes_paginas()}


def bloco_distribuicao():
    """Dashboard de distribuicao da mesma conta (regra fixa do gerar.py: DISTRIBUICAO no nome)."""
    import gerar as base
    base.CFG.clear()
    base.CFG.update({"nome": CFG["nome"], "subtitulo": "Campanhas de distribuição",
                     "conta_anuncio": CFG["conta_anuncio"], "criativos_gasto_minimo": CFG["criativos_gasto_minimo"]})
    return base.montar_dados()


def frente_de(ad):
    nome = sem_acento(f"{ad['campanha']} | {ad.get('conjunto', '')}")
    for f in CFG["frentes"]:
        if all(sem_acento(t) in nome for t in f["contem"]):
            return f["nome"]
    return "Outras"


def limpar_url(u):
    return re.sub(r"^https?://(www\.)?", "", (u or "").split("?")[0]).rstrip("/")


def destino_de(ad):
    url = limpar_url(ad.get("url"))
    for d in CFG["destinos"]:
        if d.get("campanha") and sem_acento(d["campanha"]) in sem_acento(ad["campanha"]):
            return d["nome"]
        if d.get("prefixo") and url.startswith(limpar_url(d["prefixo"])):
            return d["nome"]
    return url or "Sem link identificado"


def atleta_de(ad):
    """Cada atleta roda num conjunto proprio: o nome do conjunto, sem o prefixo de publico ("limpar" no config) e sem
    o mes no fim, vira o rotulo. Ex: 'Interesses_..._Envolvidos_Bruno_Sena_Guarda_Lapela' -> 'Bruno Sena Guarda Lapela'.
    O link da pagina do curso ('.../<curso>-com-<atleta>') entra como detalhe quando a Meta devolve (anuncio novo)."""
    nome = ad.get("conjunto") or ad["nome"]
    for p in sorted(CFG["atletas"].get("limpar", []), key=len, reverse=True):
        if nome.upper().startswith(p.upper()):
            nome = nome[len(p):]
            break
    nome = re.sub(r"_(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)_?\d{2}$", "", nome, flags=re.I)
    nome = nome.replace("_", " ").strip()
    if nome.isupper():
        nome = nome.title()
    slug = limpar_url(ad.get("url")).split("/")[-1]
    curso = slug.rsplit("-com-", 1)[0].replace("-", " ").capitalize() if "-com-" in slug else ""
    return nome, curso


def bloco_atletas(ontem):
    """Nivel de conjunto (cada atleta tem o seu), pelo mesmo motivo das frentes: bate com a campanha."""
    cfg = CFG["atletas"]
    dias = int(cfg.get("dias", 90))
    inicio = ontem - dt.timedelta(days=dias - 1)
    status = {c["id"]: c.get("effective_status", "") for c in api(
        f"{CFG['conta_anuncio']}/adsets", fields="effective_status", limit=500)}
    grupos = {}
    for l in insights("adset", inicio, ontem, "adset_id,adset_name,campaign_name," + CAMPOS_METRICAS):
        if sem_acento(cfg["campanha"]) not in sem_acento(l["campaign_name"]):
            continue
        m = metricas(l)
        if not (m["gasto"] > 0 or m["compras"] > 0):
            continue
        atleta, _ = atleta_de({"conjunto": l.get("adset_name", ""), "nome": ""})
        g = grupos.setdefault(atleta, {"atleta": atleta, "rodando": False, **dict.fromkeys(SOMAVEIS, 0.0)})
        g["rodando"] = g["rodando"] or status.get(l["adset_id"]) == "ACTIVE"
        for k in SOMAVEIS:
            g[k] += m[k]
    repasse = float(cfg.get("repasse", 0))
    for g in grupos.values():
        g["sobra"] = g["faturamento"] * (1 - repasse) - g["gasto"]
    return {"inicio": inicio.isoformat(), "fim": ontem.isoformat(), "dias": dias, "repasse": repasse,
            "campanha": cfg["campanha"], "lista": sorted(grupos.values(), key=lambda g: -g["sobra"])}


def bloco_google(periodos):
    """Google Ads separado da Meta, nos mesmos periodos. So campanhas com VENDAS no nome (mesma regra).
    Compras e faturamento so da acao de conversao de compra (a conta pode ter lead e YouTube como principais)."""
    import gerar_google as gg
    gcfg = CFG["google"]
    try:
        g = gg.Google(gcfg["customer_id"])
    except SystemExit as erro:
        print(f"  aviso: Google Ads ficou de fora ({erro})")
        return None
    onde = f"campaign.name REGEXP_MATCH '(?i).*{FILTRO}.*'"
    acao = gcfg.get("acao_compra", "purchase").lower()
    met = "metrics.cost_micros, metrics.impressions, metrics.clicks"

    def tipo(canal):
        return {"PERFORMANCE_MAX": "Performance Max", "SEARCH": "Pesquisa", "DISPLAY": "Display", "VIDEO": "YouTube",
                "DEMAND_GEN": "Demand Gen", "SHOPPING": "Shopping"}.get(canal, canal.title())

    def compras(ini, fim, por):
        """{chave: [compras, faturamento]} so da acao de compra."""
        out = {}
        for r in g.q(f"SELECT {por}, segments.conversion_action_name, metrics.conversions, metrics.conversions_value "
                     f"FROM campaign WHERE {gg.entre(ini, fim)} AND {onde} AND metrics.conversions > 0"):
            if acao in r["segments"]["conversionActionName"].lower():
                k = r["campaign"]["name"] if por == "campaign.name" else "total"
                c = out.setdefault(k, [0.0, 0.0])
                c[0] += float(r["metrics"]["conversions"])
                c[1] += float(r["metrics"].get("conversionsValue", 0))
        return out

    def campanhas(ini, fim):
        cp = compras(ini, fim, "campaign.name")
        lista = []
        for r in g.q(f"SELECT campaign.name, campaign.status, campaign.advertising_channel_type, {met} "
                     f"FROM campaign WHERE {gg.entre(ini, fim)} AND {onde} AND metrics.impressions > 0"):
            n = r["campaign"]["name"]
            lista.append({"nome": n, "status": r["campaign"].get("status", ""),
                          "tipo": tipo(r["campaign"].get("advertisingChannelType", "")),
                          "gasto": gg.reais(r["metrics"]), "impressoes": int(r["metrics"].get("impressions", 0)),
                          "cliques": int(r["metrics"].get("clicks", 0)),
                          "compras": cp.get(n, [0, 0])[0], "faturamento": cp.get(n, [0, 0])[1]})
        return sorted(lista, key=lambda c: -c["gasto"])

    def total(lista):
        return {k: sum(c[k] for c in lista) for k in ("gasto", "impressoes", "cliques", "compras", "faturamento")}

    def destinos(ini, fim):
        """Valor de conversao por pagina final dos anuncios (Pesquisa, Demand Gen) e grupos de recursos (PMax)."""
        por = {}
        consultas = [("ad_group_ad.ad.final_urls", "ad_group_ad", "adGroupAd"),
                     ("asset_group.final_urls", "asset_group", "assetGroup")]
        for campo, recurso, chave in consultas:
            for r in g.q(f"SELECT {campo}, {met}, metrics.conversions_value FROM {recurso} "
                         f"WHERE {gg.entre(ini, fim)} AND {onde} AND metrics.impressions > 0"):
                obj = r[chave].get("ad", r[chave])
                url = limpar_url((obj.get("finalUrls") or [""])[0]) or "Sem link"
                d = por.setdefault(url, {"nome": url, "gasto": 0.0, "faturamento": 0.0})
                d["gasto"] += gg.reais(r["metrics"])
                d["faturamento"] += float(r["metrics"].get("conversionsValue", 0))
        return sorted(por.values(), key=lambda d: -d["faturamento"])

    saida = {}
    for k, p in periodos.items():
        ini, fim = dt.date.fromisoformat(p["inicio"]), dt.date.fromisoformat(p["fim"])
        camps = campanhas(ini, fim)
        termos = gg.termos(g.q("SELECT campaign.name, campaign.advertising_channel_type, campaign_search_term_view.search_term, "
                               "metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions "
                               f"FROM campaign_search_term_view WHERE {gg.entre(ini, fim)} AND {onde}"))
        saida[k] = {"atual": total(camps),
                    "anterior": total(campanhas(dt.date.fromisoformat(p["ant_inicio"]), dt.date.fromisoformat(p["ant_fim"]))),
                    "campanhas": camps, "destinos": destinos(ini, fim),
                    "termos": sorted([t for t in termos if t["conv"] > 0], key=lambda t: -t["conv"])[:15]}
    return {"conta": gcfg["customer_id"], "nome": gcfg.get("nome", "Google Ads"), "periodos": saida}


def periodo(dias, ontem, status_camp, segmentos, inicio=None):
    fim = ontem
    if inicio:
        dias = (fim - inicio).days + 1
    inicio = fim - dt.timedelta(days=dias - 1)
    ant_fim = inicio - dt.timedelta(days=1)
    ant_inicio = ant_fim - dt.timedelta(days=dias - 1)

    campanhas = []
    for l in insights("campaign", inicio, fim, "campaign_id,campaign_name," + CAMPOS_METRICAS):
        conferir(eh_vendas(l["campaign_name"]), f"campanha fora do filtro: {l['campaign_name']}")
        m = metricas(l)
        # Campanha sem gasto no periodo mas com compra (clique de dias antes) entra, igual aos conjuntos e anuncios:
        # senao o faturamento das frentes nao bate com o das campanhas (BJJ, Esquenta RMK, 14/11/2026).
        if m["gasto"] > 0 or m["compras"] > 0:
            campanhas.append({"id": l["campaign_id"], "nome": l["campaign_name"],
                              "temp": temperatura(l["campaign_name"]),
                              "status": status_camp.get(l["campaign_id"], ""), **m})
    campanhas.sort(key=lambda c: -c["gasto"])

    anuncios = []
    for l in insights("ad", inicio, fim, "ad_id,ad_name,adset_name,campaign_name," + CAMPOS_METRICAS):
        conferir(eh_vendas(l["campaign_name"]), f"anuncio de campanha fora do filtro: {l['campaign_name']}")
        m = metricas(l)
        # Compra sem gasto no periodo (clique de dias antes) tambem entra: frentes e destinos somam o faturamento
        # inteiro. O ranking de criativos continua exigindo o gasto minimo.
        if m["gasto"] > 0 or m["compras"] > 0:
            anuncios.append({"id": l["ad_id"], "nome": l["ad_name"], "campanha": l["campaign_name"],
                             "conjunto": l.get("adset_name", ""), "temp": temperatura(l["campaign_name"]), **m})

    # Frentes saem do nivel de conjunto: a Meta atribui a compra no conjunto igual a campanha, mas no nivel de
    # anuncio pode jogar a mesma compra para outra campanha (visto na BJJ em 13/11/2026).
    conjuntos = []
    if CFG.get("frentes"):
        for l in insights("adset", inicio, fim, "adset_id,adset_name,campaign_name," + CAMPOS_METRICAS):
            m = metricas(l)
            if m["gasto"] > 0 or m["compras"] > 0:
                c = {"campanha": l["campaign_name"], "conjunto": l.get("adset_name", ""),
                     "temp": temperatura(l["campaign_name"]), **m}
                conjuntos.append(c | {"frente": frente_de(c)})
        for k in ("gasto", "faturamento"):
            conferir(abs(sum(c[k] for c in conjuntos) - sum(c[k] for c in campanhas)) < 1,
                     f"frentes {dias}d: {k} dos conjuntos difere da soma das campanhas")

    # Abas por tipo de campanha: resumo de cada aba = soma das campanhas dela, no periodo e no anterior.
    paginas = None
    if CFG.get("paginas"):
        anteriores = [{"nome": l["campaign_name"], **metricas(l)} for l in
                      insights("campaign", ant_inicio, ant_fim, "campaign_id,campaign_name," + CAMPOS_METRICAS)]
        for item in campanhas + anuncios + conjuntos:
            item["pagina"] = pagina_de(item.get("campanha") or item["nome"])
        paginas = {n: {"atual": somar(c for c in campanhas if c["pagina"] == n),
                       "anterior": somar(c for c in anteriores if pagina_de(c["nome"]) == n)}
                   for n in nomes_paginas()}

    atual = conta_total(inicio, fim)
    soma = sum(c["gasto"] for c in campanhas)
    conferir(abs(soma - atual["gasto"]) < 0.05,
             f"total {dias}d (R$ {atual['gasto']:.2f}) difere da soma das campanhas VENDAS (R$ {soma:.2f})")

    seg = {}
    for nome, ids in segmentos.items():
        seg_atual = conta_total(inicio, fim, ids)
        conferir(abs(seg_atual["gasto"] - sum(c["gasto"] for c in campanhas if c["temp"] == nome)) < 0.05,
                 f"segmento {nome} {dias}d difere da soma das campanhas {nome}")
        seg[nome] = {"atual": seg_atual, "anterior": conta_total(ant_inicio, ant_fim, ids)}

    return {
        "segmentos": seg,
        "inicio": inicio.isoformat(), "fim": fim.isoformat(),
        "ant_inicio": ant_inicio.isoformat(), "ant_fim": ant_fim.isoformat(),
        "atual": atual,
        "anterior": conta_total(ant_inicio, ant_fim),
        "campanhas": campanhas,
        "anuncios": anuncios,
        **({"conjuntos": conjuntos} if CFG.get("frentes") else {}),
        **({"paginas": paginas} if paginas else {}),
    }


def bloco_hotmart(slug, periodos, fuso):
    """Vendas reais da Hotmart em cada periodo, pra comparar com as compras do pixel das campanhas do dashboard.
    A Hotmart conta vendas de qualquer origem (anuncio, organico, link direto)."""
    if not gerar_hotmart.credencial(slug):
        print(f"::warning::vendas/{slug} saiu sem a Hotmart: faltam os secrets HOTMART_{slug.upper().replace('-', '_')}_*")
        return None
    produtos = CFG["hotmart"]["produtos"]
    inicio = min(dt.date.fromisoformat(p["inicio"]) for p in periodos.values())
    fim = max(dt.date.fromisoformat(p["fim"]) for p in periodos.values())
    linhas = gerar_hotmart.vendas(gerar_hotmart.Hotmart(slug), list(produtos), inicio, fim, fuso)
    for p in periodos.values():
        p["hotmart"] = gerar_hotmart.resumo(linhas, dt.date.fromisoformat(p["inicio"]), dt.date.fromisoformat(p["fim"]))
    return {"produtos": list(produtos.values())}


def serie_diaria(ontem, ids=None):
    inicio = ontem - dt.timedelta(days=29)
    por_dia = {l["date_start"]: metricas(l) for l in diario_seguro(
        lambda c, i, f, **e: insights("account", i, f, c, ids=ids, **e), CAMPOS_METRICAS, inicio, ontem)}
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

    todas = api(f"{CFG['conta_anuncio']}/campaigns", fields="name,effective_status", limit=500)
    status_camp = {c["id"]: c["effective_status"] for c in todas}
    CFG["_ids_filtro"] = [c["id"] for c in todas if eh_vendas(c["name"])]
    # Filtro quente/frio: so aparece quando o nome das campanhas VENDAS traz Q (quente) ou ASP/F (frio).
    segmentos = {}
    for nome in ("quente", "frio"):
        ids = [c["id"] for c in todas if eh_vendas(c["name"]) and temperatura(c["name"]) == nome]
        if ids:
            segmentos[nome] = ids
    periodos = {str(d): periodo(d, ontem, status_camp, segmentos) for d in PERIODOS}
    if CFG.get("desde"):
        periodos["desde"] = periodo(0, ontem, status_camp, segmentos, dt.date.fromisoformat(CFG["desde"]))
    if CFG.get("mes_atual"):
        periodos["mes"] = periodo(0, ontem, status_camp, segmentos, ontem.replace(day=1))

    # Detalhes dos criativos uma vez so, pra todos os anuncios de todos os periodos.
    todos = {a["id"]: a for p in periodos.values() for a in p["anuncios"]}
    detalhes_anuncios(list(todos.values()))
    chaves = ("status", "thumb", "instagram", "previa") + (("url",) if precisa_url() else ())
    for p in periodos.values():
        for a in p["anuncios"]:
            a.update({k: todos[a["id"]][k] for k in chaves})
        # Destino vai em cada anuncio (o link e do anuncio): o template agrupa, inclusive com o filtro quente/frio.
        if CFG.get("destinos"):
            for a in p["anuncios"]:
                a["destino"] = destino_de(a)

    hotmart = bloco_hotmart(slug, periodos, fuso) if CFG.get("hotmart") else None
    atletas = bloco_atletas(ontem) if CFG.get("atletas") else None
    google = bloco_google(periodos) if CFG.get("google") else None

    dados = {
        "nome": CFG["nome"],
        "subtitulo": CFG.get("subtitulo", ""),
        "conta": conta.get("name", ""),
        "moeda": conta.get("currency", "BRL"),
        "filtro": FILTRO,
        "filtros": filtros(),
        "excluir": excluidos(),
        "so_dados": bool(CFG.get("so_dados")),
        "hotmart": hotmart,
        "atletas": atletas,
        "google": google,
        "regua_roas": CFG.get("regua_roas"),
        "faturamento_real": CFG.get("faturamento_real"),
        "gerado_em": agora.isoformat(timespec="minutes"),
        "gasto_minimo_criativo": CFG["criativos_gasto_minimo"],
        "periodos": periodos,
        "diario": serie_diaria(ontem),
    }
    if segmentos:
        dados["diario_segmentos"] = {n: serie_diaria(ontem, ids) for n, ids in segmentos.items()}
    if CFG.get("paginas"):
        dados["paginas"] = nomes_paginas()
        dados["diario_paginas"] = diario_paginas(ontem)
        soma = sum(d["gasto"] for serie in dados["diario_paginas"].values() for d in serie)
        conferir(abs(soma - periodos["30"]["atual"]["gasto"]) < 0.05, "dia a dia das abas nao bate com o total de 30 dias")
    if CFG.get("distribuicao"):
        dados["distribuicao"] = bloco_distribuicao()
    soma_diaria = sum(d["gasto"] for d in dados["diario"])
    conferir(abs(soma_diaria - periodos["30"]["atual"]["gasto"]) < 0.05,
             "serie diaria nao bate com o total de 30 dias")

    json_dados = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    senha = os.environ.get(f"SENHA_VENDAS_{slug.upper().replace('-', '_')}", "")
    # Com faturamento do cliente na pagina: sem o secret da senha, nao gera (nao publica aberto por engano).
    # Pre-visualizacao local: PREVIA_SEM_SENHA=1.
    conferir(senha or not CFG.get("senha_obrigatoria") or os.environ.get("PREVIA_SEM_SENHA") == "1",
             f"falta o secret SENHA_VENDAS_{slug.upper().replace('-', '_')} (senha_obrigatoria no config)")
    payload = criptografar(json_dados, senha) if senha else {"enc": False, "dados": dados}

    saida.mkdir(parents=True, exist_ok=True)
    html = (AQUI / "template_vendas.html").read_text(encoding="utf-8")
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    # Aba Distribuicao: o template aprovado de distribuicao vai como texto (sem dados); os dados vem do payload
    # (com a senha) e a pagina e montada num iframe quando a aba abre.
    dist = "null"
    if CFG.get("distribuicao"):
        interno = (AQUI / "template.html").read_text(encoding="utf-8").replace(
            "</head>", "<style>html,body{background:transparent!important;min-height:0!important}</style></head>", 1)
        dist = json.dumps(interno, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("__TEMPLATE_DIST__", dist)
    (saida / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(AQUI / "logo.png", saida / "logo.png")

    p30 = periodos["30"]["atual"]
    if google:
        g30 = google["periodos"]["30"]["atual"]
        print(f"   + Google: 30d R$ {g30['gasto']:.2f}, {g30['compras']:.0f} compras, "
              f"{len(google['periodos']['30']['campanhas'])} campanhas com {FILTRO}")
    if atletas:
        print(f"   + Atletas: {len(atletas['lista'])} em {atletas['dias']} dias")
    if CFG.get("paginas"):
        print("   + Abas: " + ", ".join(f"{n} R$ {periodos['30']['paginas'][n]['atual']['gasto']:.0f}"
                                      for n in dados["paginas"]))
    if dados.get("distribuicao"):
        d30 = dados["distribuicao"]["periodos"]["30"]
        print(f"   + Distribuicao: 30d R$ {d30['atual']['gasto']:.2f}, {len(d30['campanhas'])} campanhas")
    if hotmart:
        ref = periodos.get("desde", periodos["30"])
        print(f"   + Hotmart: {ref['inicio']} a {ref['fim']} {ref['hotmart']['vendas']} vendas "
              f"x {ref['atual']['compras']:.0f} compras no pixel")
    print(f"OK vendas/{slug}: 30d R$ {p30['gasto']:.2f}, {p30['compras']:.0f} compras, "
          f"{len(periodos['30']['campanhas'])} campanhas, {len(periodos['30']['anuncios'])} anuncios | "
          f"{'com senha' if senha else 'link aberto, sem senha'}")


def main():
    todos = sorted(p.parent.name for p in (AQUI / "vendas").glob("*/config.json"))
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
