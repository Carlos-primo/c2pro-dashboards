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
  vendas_reais     opcional: {"arquivo": "vendas.json", "ticket_liquido": 102.93, "preco": 117}
                   ingressos vendidos de verdade (planilha do cliente) no lugar das compras do pixel no total
                   do funil, e faturamento = ingressos x ticket liquido. O arquivo fica na pasta do lancamento,
                   so com a contagem por dia (sem e-mail nem telefone: o repositorio e publico).
                   Tambem liga a aba "Geral": todo o investimento da conta no lancamento contra as vendas reais
  distribuicao     opcional: true liga a aba "Distribuicao" (campanhas com DISTRIBUICAO no nome, regras do gerar.py)

Regra: o funil, as tabelas e os criativos so usam campanhas com CAPTACAO (ou o filtro_campanha do config) e o
codigo do lancamento no nome. A aba Distribuicao so usa DISTRIBUICAO. So a aba Geral soma a conta inteira.

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

import gerar
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


def insights_conta(nivel, inicio, fim, campos, **extra):
    """Insights da conta inteira, sem filtro de campanha. So para a aba Geral."""
    return api(
        f"{CFG['conta_anuncio']}/insights",
        level=nivel,
        fields=campos,
        time_range=json.dumps({"since": inicio.isoformat(), "until": fim.isoformat()}),
        limit=500,
        **extra,
    )


def tipo_campanha(nome):
    if eh_do_lancamento(nome):
        return "captacao"
    return "distribuicao" if gerar.FILTRO in sem_acento(nome) else "outras"


def geral(inicio, fim):
    """Tudo que a conta investiu no periodo, campanha por campanha, separado em captacao, distribuicao e outras."""
    campanhas = []
    for l in insights_conta("campaign", inicio, fim, "campaign_id,campaign_name,spend,impressions"):
        gasto = round(float(l.get("spend", 0)), 2)
        if gasto > 0:
            campanhas.append({"id": l["campaign_id"], "nome": l["campaign_name"], "tipo": tipo_campanha(l["campaign_name"]),
                              "gasto": gasto, "impressoes": int(l.get("impressions", 0))})
    campanhas.sort(key=lambda c: -c["gasto"])
    total = insights_conta("account", inicio, fim, "spend")
    gasto = round(float(total[0].get("spend", 0)), 2) if total else 0.0
    conferir(abs(sum(c["gasto"] for c in campanhas) - gasto) < 0.05 + 0.01 * len(campanhas),
             f"geral {inicio}..{fim}: total da conta (R$ {gasto:.2f}) difere da soma das campanhas")
    return {"gasto": gasto, "campanhas": campanhas}


def distribuicao(inicio, fim, comparar):
    """Metricas de distribuicao do periodo, pelas mesmas regras e funcoes do gerar.py (so DISTRIBUICAO)."""
    gerar.CFG.clear()
    gerar.CFG.update({"nome": CFG["nome"], "conta_anuncio": CFG["conta_anuncio"]})
    p = gerar.periodo_entre(inicio, fim)
    if not comparar:
        p.update({"ant_inicio": None, "ant_fim": None, "anterior": None})
    return p


def vendas_reais(slug, fim_lanc):
    """Ingressos vendidos da planilha do cliente, ja contados por dia (ver docstring do modulo)."""
    vr = CFG.get("vendas_reais")
    if not vr:
        return None
    arq = json.loads((AQUI / "lancamento" / slug / vr.get("arquivo", "vendas.json")).read_text(encoding="utf-8"))
    por_dia = {d: int(n) for d, n in arq["por_dia"].items()}
    conferir(all(dt.date.fromisoformat(d) <= fim_lanc for d in por_dia), "vendas.json tem dia depois de ontem")
    return {"ticket_liquido": vr["ticket_liquido"], "preco": vr["preco"], "fonte": arq.get("fonte", ""),
            "ate": arq["ate"], "por_dia": por_dia}


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
    if CFG.get("vendas_reais"):
        p["geral"] = geral(inicio, fim)
        p["geral_ant"] = geral(ant_inicio, ant_fim) if comparar else None
    if CFG.get("distribuicao"):
        p["distribuicao"] = distribuicao(inicio, fim, comparar)
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

    extras = {}
    if CFG.get("vendas_reais"):
        extras["vendas_reais"] = vendas_reais(slug, fim_lanc)
        gasto_dia = {l["date_start"]: float(l.get("spend", 0)) for l in diario_seguro(
            lambda c, i, f, **e: insights_conta("account", i, f, c, **e), "spend", inicio_lanc, fim_lanc)}
        extras["diario_geral"] = [{"data": d["data"], "gasto": round(gasto_dia.get(d["data"], 0.0), 2)} for d in diario]
        conferir(abs(sum(d["gasto"] for d in extras["diario_geral"]) - periodos["todo"]["geral"]["gasto"]) < 0.05,
                 "serie diaria da conta nao bate com o total geral do lancamento")
    if CFG.get("distribuicao"):
        extras["diario_distribuicao"] = gerar.serie_diaria(inicio_lanc, fim_lanc)
        conferir(abs(sum(d["gasto"] for d in extras["diario_distribuicao"])
                     - periodos["todo"]["distribuicao"]["atual"]["gasto"]) < 0.05,
                 "serie diaria da distribuicao nao bate com o total do lancamento")

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
        **extras,
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
    reais = f" (planilha: {sum(extras['vendas_reais']['por_dia'].values())})" if "vendas_reais" in extras else ""
    print(f"OK lancamento/{slug}: {inicio_lanc} a {fim_lanc} R$ {t['gasto']:.2f}, {t['compras']:.0f} compras{reais}, "
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
