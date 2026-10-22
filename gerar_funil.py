"""Dashboard de distribuicao por FUNIL (topo, meio e fundo) com abas de Meta Ads e Google Ads.

Ativado por cliente com "modelo": "funil" no clientes/<slug>/config.json. O gerar.py chama gerar() aqui
no lugar do dashboard padrao, entao o link e o agendamento (2h) continuam os mesmos.

config.json:
  {"nome": "...", "subtitulo": "...", "conta_anuncio": "act_...", "criativos_gasto_minimo": 10,
   "modelo": "funil", "instagram_id": "1784...", "google_customer_id": "4112396584"}

Meta: investimento, funil (impressoes > views 3s > ThruPlay > interacoes), engajamento e criativos por etapa.
Topo ainda traz seguidores novos do perfil e custo por seguidor (Instagram). Interacoes = curtidas + comentarios
+ compartilhamentos + salvamentos (post_interaction_gross da Meta).
Google: campanhas com DISTRIBUICAO no nome (YouTube), views, taxa de view, custo por view, quartis e inscricoes.
Se o Google falhar (credencial), a aba some e o resto publica normalmente.
"""

import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import gerar as base

AQUI = Path(__file__).resolve().parent
ETAPAS = ("topo", "meio", "fundo", "geral")
SOMAVEIS = ("gasto", "impressoes", "alcance", "views3s", "p25", "p50", "p75", "p100", "thruplay",
            "interacoes", "reacoes", "comentarios", "compartilhamentos", "salvos", "cliques", "contatos")
CAMPOS = base.CAMPOS_METRICAS


def etapa_da(nome):
    n = (nome or "").upper()
    for e in ("TOPO", "MEIO", "FUNDO"):
        if e in n:
            return e.lower()
    return "geral"


def metricas(linha):
    m = base.metricas(linha)
    a = {x["action_type"]: float(x["value"]) for x in linha.get("actions", [])}
    return {
        **{k: m[k] for k in ("gasto", "impressoes", "alcance", "views3s", "p25", "p50", "p75", "p100", "thruplay")},
        "interacoes": a.get("post_interaction_gross", 0),
        "reacoes": a.get("post_reaction", 0),
        "comentarios": a.get("comment", 0),
        "compartilhamentos": a.get("post", 0),
        "salvos": a.get("onsite_conversion.post_save", 0),
        "cliques": a.get("link_click", 0),
        "contatos": a.get("onsite_conversion.messaging_conversation_started_7d", 0),
    }


def somar(linhas):
    return {k: sum(l[k] for l in linhas) for k in SOMAVEIS}


# ---------- Instagram (seguidores e visitas ao perfil) ----------
def ig_get(ig_id, metrica, ini, fim, **extra):
    """Insights do Instagram entre duas datas (fim inclusivo). Devolve a lista de valores ou None se falhar."""
    fuso = dt.timezone(dt.timedelta(hours=-3))
    ts = lambda d: int(dt.datetime(d.year, d.month, d.day, tzinfo=fuso).timestamp())
    r = requests.get(f"{base.BASE}/{ig_id}/insights", timeout=60, params={
        "metric": metrica, "since": ts(ini), "until": ts(fim + dt.timedelta(days=1)),
        "access_token": base.TOKEN, **extra}).json()
    if "error" in r:
        print(f"  aviso: Instagram {metrica}: {r['error'].get('message')}")
        return None
    return r.get("data", [])


def seguidores_por_dia(ig_id, ontem):
    """Seguidores novos por dia (follower_count). A API so entrega os ultimos 30 dias, sem hoje."""
    por_dia = {}
    for item in ig_get(ig_id, "follower_count", ontem - dt.timedelta(days=29), ontem, period="day") or []:
        for v in item.get("values", []):
            # o Instagram carimba cada valor com o fim do dia (07:00 UTC): usa a data do carimbo
            dia = dt.datetime.fromisoformat(v["end_time"].replace("+0000", "+00:00")).date().isoformat()
            por_dia[dia] = v["value"]
    return por_dia


def visitas_perfil(ig_id, ini, fim):
    dados = ig_get(ig_id, "profile_views", ini, fim, period="day", metric_type="total_value")
    try:
        return dados[0]["total_value"]["value"]
    except (TypeError, IndexError, KeyError):
        return None


def soma_dias(por_dia, ini, fim):
    """Soma dos dias do periodo, ou None se algum dia nao veio (nao compara com dado incompleto)."""
    dias = [(ini + dt.timedelta(days=n)).isoformat() for n in range((fim - ini).days + 1)]
    return sum(por_dia[d] for d in dias) if all(d in por_dia for d in dias) else None


# ---------- Meta ----------
def por_etapa(linhas):
    grupos = {}
    for l in linhas:
        grupos.setdefault(etapa_da(l["nome"]), []).append(l)
    return {e: somar(ls) for e, ls in grupos.items()}


def campanhas_do_periodo(ini, fim):
    status = {c["id"]: c["effective_status"] for c in base.api(
        f"{base.CFG['conta_anuncio']}/campaigns", fields="effective_status", limit=500)}
    lista = []
    for l in base.insights("campaign", ini, fim, "campaign_id,campaign_name," + CAMPOS):
        base.conferir(base.eh_distribuicao(l["campaign_name"]), f"campanha fora do filtro: {l['campaign_name']}")
        m = metricas(l)
        if m["gasto"] > 0:
            lista.append({"id": l["campaign_id"], "nome": l["campaign_name"], "etapa": etapa_da(l["campaign_name"]),
                          "status": status.get(l["campaign_id"], ""), **m})
    return sorted(lista, key=lambda c: -c["gasto"])


def periodo_meta(dias, ontem, ig_id, seg_dia):
    fim = ontem
    ini = fim - dt.timedelta(days=dias - 1)
    pfim = ini - dt.timedelta(days=1)
    pini = pfim - dt.timedelta(days=dias - 1)

    camp, camp_ant = campanhas_do_periodo(ini, fim), campanhas_do_periodo(pini, pfim)
    anuncios = []
    for l in base.insights("ad", ini, fim, "ad_id,ad_name,campaign_name," + CAMPOS):
        base.conferir(base.eh_distribuicao(l["campaign_name"]), f"anuncio fora do filtro: {l['campaign_name']}")
        m = metricas(l)
        if m["gasto"] > 0:
            anuncios.append({"id": l["ad_id"], "nome": l["ad_name"], "campanha": l["campaign_name"],
                             "etapa": etapa_da(l["campaign_name"]), **m})
    base.detalhes_anuncios(anuncios)

    total = base.conta_total(ini, fim)["gasto"]
    soma = sum(c["gasto"] for c in camp)
    base.conferir(abs(soma - total) < 0.05, f"{dias}d: total (R$ {total:.2f}) difere da soma das campanhas (R$ {soma:.2f})")

    atual, ant = por_etapa(camp), por_etapa(camp_ant)
    etapas = {}
    for e in ETAPAS:
        if e in atual or e in ant:
            etapas[e] = {
                "atual": atual.get(e, somar([])), "anterior": ant.get(e, somar([])),
                "campanhas": [c for c in camp if c["etapa"] == e],
                "anuncios": [a for a in anuncios if a["etapa"] == e],
            }
    ig = {"seguidores": soma_dias(seg_dia, ini, fim), "seguidores_ant": soma_dias(seg_dia, pini, pfim),
          "visitas": visitas_perfil(ig_id, ini, fim) if ig_id else None,
          "visitas_ant": visitas_perfil(ig_id, pini, pfim) if ig_id else None}
    return {"inicio": ini.isoformat(), "fim": fim.isoformat(), "ant_inicio": pini.isoformat(),
            "ant_fim": pfim.isoformat(), "etapas": etapas, "ig": ig}


def serie_meta(ontem, seg_dia):
    ini = ontem - dt.timedelta(days=29)
    linhas = base.diario_seguro(lambda c, i, f, **e: base.insights("account", i, f, c, **e),
                                "spend,impressions,actions", ini, ontem)
    por_dia = {l["date_start"]: l for l in linhas}
    dias = []
    for n in range(30):
        d = (ini + dt.timedelta(days=n)).isoformat()
        a = {x["action_type"]: float(x["value"]) for x in por_dia.get(d, {}).get("actions", [])}
        dias.append({"data": d, "gasto": float(por_dia.get(d, {}).get("spend", 0)),
                     "interacoes": a.get("post_interaction_gross", 0), "views3s": a.get("video_view", 0),
                     "seguidores": seg_dia.get(d)})
    return dias


# ---------- Google Ads ----------
def google(customer_id, ontem):
    import gerar_google as gg
    try:
        g = gg.Google(customer_id)
    except SystemExit as erro:
        print(f"  aviso: Google Ads ficou de fora ({erro})")
        return None

    filtro = "campaign.name LIKE '%DISTRIBUICAO%'"
    campos = ("campaign.name, campaign.status, metrics.cost_micros, metrics.impressions, metrics.clicks, "
              "metrics.video_trueview_views, metrics.engagements, metrics.conversions, "
              "metrics.video_quartile_p25_rate, metrics.video_quartile_p50_rate, "
              "metrics.video_quartile_p75_rate, metrics.video_quartile_p100_rate")

    def consulta(ini, fim):
        camps = []
        for r in g.q(f"SELECT {campos} FROM campaign WHERE {gg.entre(ini, fim)} AND {filtro} AND metrics.impressions > 0"):
            m = r["metrics"]
            camps.append({
                "nome": r["campaign"]["name"], "status": r["campaign"].get("status", ""),
                "custo": gg.reais(m), "imp": int(m.get("impressions", 0)), "cli": int(m.get("clicks", 0)),
                "views": int(m.get("videoTrueviewViews", 0)), "eng": int(m.get("engagements", 0)),
                "conv": float(m.get("conversions", 0)),
                "q": [float(m.get(k, 0)) for k in ("videoQuartileP25Rate", "videoQuartileP50Rate",
                                                   "videoQuartileP75Rate", "videoQuartileP100Rate")]})
        acoes = {}
        for r in g.q("SELECT segments.conversion_action_name, metrics.conversions FROM campaign "
                     f"WHERE {gg.entre(ini, fim)} AND {filtro} AND metrics.conversions > 0"):
            n = r["segments"]["conversionActionName"]
            acoes[n] = acoes.get(n, 0) + float(r["metrics"]["conversions"])
        return camps, acoes

    def total(camps):
        imp = sum(c["imp"] for c in camps)
        t = {k: sum(c[k] for c in camps) for k in ("custo", "imp", "cli", "views", "eng", "conv")}
        t["q"] = [sum(c["q"][i] * c["imp"] for c in camps) / imp if imp else 0 for i in range(4)]
        return t

    periodos = {}
    for dias in (7, 30):
        ini = ontem - dt.timedelta(days=dias - 1)
        pfim = ini - dt.timedelta(days=1)
        pini = pfim - dt.timedelta(days=dias - 1)
        camps, acoes = consulta(ini, ontem)
        camps_ant, _ = consulta(pini, pfim)
        t = total(camps)
        base.conferir(abs(sum(acoes.values()) - t["conv"]) < 0.01, f"Google {dias}d: conversoes por tipo nao fecham")
        periodos[str(dias)] = {"inicio": ini.isoformat(), "fim": ontem.isoformat(), "ant_inicio": pini.isoformat(),
                               "ant_fim": pfim.isoformat(), "atual": t, "anterior": total(camps_ant),
                               "campanhas": sorted(camps, key=lambda c: -c["custo"]), "acoes": acoes}

    ini30 = ontem - dt.timedelta(days=29)
    por_dia = {}
    for r in g.q("SELECT segments.date, metrics.cost_micros, metrics.impressions, metrics.video_trueview_views, "
                 f"metrics.conversions FROM campaign WHERE {gg.entre(ini30, ontem)} AND {filtro}"):
        m = r["metrics"]
        d = por_dia.setdefault(r["segments"]["date"], {"custo": 0.0, "imp": 0, "views": 0, "conv": 0.0})
        d["custo"] += gg.reais(m)
        d["imp"] += int(m.get("impressions", 0))
        d["views"] += int(m.get("videoTrueviewViews", 0))
        d["conv"] += float(m.get("conversions", 0))
    diario = []
    for n in range(30):
        d = (ini30 + dt.timedelta(days=n)).isoformat()
        diario.append({"data": d, **por_dia.get(d, {"custo": 0.0, "imp": 0, "views": 0, "conv": 0.0})})
    base.conferir(abs(sum(d["custo"] for d in diario) - periodos["30"]["atual"]["custo"]) < 0.05,
                  "Google: serie diaria nao bate com o total de 30 dias")
    return {"periodos": periodos, "diario": diario, "conta": customer_id}


def gerar(slug):
    cfg = json.loads((AQUI / "clientes" / slug / "config.json").read_text(encoding="utf-8"))
    base.CFG.clear()
    base.CFG.update(cfg)

    conta = base.api(cfg["conta_anuncio"], fields="name,currency,timezone_name")
    agora = dt.datetime.now(ZoneInfo(conta.get("timezone_name") or "America/Sao_Paulo"))
    ontem = agora.date() - dt.timedelta(days=1)

    ig_id = cfg.get("instagram_id")
    seg_dia = seguidores_por_dia(ig_id, ontem) if ig_id else {}
    periodos = {str(d): periodo_meta(d, ontem, ig_id, seg_dia) for d in (7, 30)}
    diario = serie_meta(ontem, seg_dia)
    base.conferir(abs(sum(d["gasto"] for d in diario) - sum(
        e["atual"]["gasto"] for e in periodos["30"]["etapas"].values())) < 0.05, "serie diaria nao bate com o total de 30 dias")

    dados = {
        "nome": cfg["nome"], "subtitulo": cfg.get("subtitulo", ""), "moeda": conta.get("currency", "BRL"),
        "gerado_em": agora.isoformat(timespec="minutes"), "gasto_minimo_criativo": cfg["criativos_gasto_minimo"],
        "meta": {"periodos": periodos, "diario": diario, "tem_ig": bool(ig_id and seg_dia)},
        "google": google(cfg["google_customer_id"], ontem) if cfg.get("google_customer_id") else None,
    }
    json_dados = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    senha = os.environ.get(f"SENHA_{slug.upper().replace('-', '_')}", "")
    payload = base.criptografar(json_dados, senha) if senha else {"enc": False, "dados": dados}

    saida = base.SITE / slug
    saida.mkdir(parents=True, exist_ok=True)
    html = (AQUI / "template_funil.html").read_text(encoding="utf-8")
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    (saida / "index.html").write_text(html, encoding="utf-8")
    base.shutil.copy(AQUI / "logo.png", saida / "logo.png")

    p = periodos["30"]
    g = dados["google"]
    print(f"OK {slug} (funil): Meta 30d R$ {sum(e['atual']['gasto'] for e in p['etapas'].values()):.2f}, "
          f"etapas {','.join(p['etapas'])}, seguidores {p['ig']['seguidores']} | "
          f"Google {'30d R$ %.2f' % g['periodos']['30']['atual']['custo'] if g else 'fora'} | "
          f"{'com senha' if senha else 'link aberto, sem senha'}")


if __name__ == "__main__":
    for s in sys.argv[1:]:
        gerar(s)
