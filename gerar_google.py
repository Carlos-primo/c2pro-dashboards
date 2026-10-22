"""Gera os dashboards de CONTATOS do Google Ads direto da API do Google, sem Claude no meio.

Cada pasta google/<slug>/ com um config.json vira um dashboard em site/google/<slug>/index.html,
publicado em https://carlos-primo.github.io/c2pro-dashboards/google/<slug>/

Uso local:   python gerar_google.py            (todos os clientes de Google)
             python gerar_google.py luke-garcia  (so um)

Mostra contatos (conversoes do Google, ex: clique no WhatsApp), custo por contato, taxa de conversao,
contatos por dia, campanhas e termos de pesquisa. Periodos: ultimos 7 e 30 dias, sempre terminando ontem
e comparados com o periodo anterior de mesmo tamanho.

config.json: {"nome": "...", "subtitulo": "...", "customer_id": "3055748705"}

Variaveis de ambiente (secrets do repositorio): GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET,
GOOGLE_ADS_REFRESH_TOKEN e, se a conta for acessada por MCC, GOOGLE_ADS_LOGIN_CUSTOMER_ID.
Desde 09/2026 a API nao exige developer token.
"""

import collections
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

AQUI = Path(__file__).resolve().parent
SITE = AQUI / "site" / "google"
API = "https://googleads.googleapis.com/v25"
PERIODOS = [7, 30]


def conferir(condicao, mensagem):
    """Aborta a geracao em vez de publicar um dashboard com dado inconsistente."""
    if not condicao:
        sys.exit(f"ERRO de consistencia: {mensagem}")


def token_acesso():
    faltando = [v for v in ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN")
                if not os.environ.get(v)]
    if faltando:
        sys.exit("ERRO: faltam as variaveis " + ", ".join(faltando))
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": os.environ["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_ADS_REFRESH_TOKEN"],
        "grant_type": "refresh_token"})
    if r.status_code != 200:
        sys.exit(f"ERRO ao renovar o token do Google ({r.status_code}): refresh token vencido ou revogado. "
                 "Gerar de novo com o setup.py oauth da skill google-ads e atualizar o secret.")
    return r.json()["access_token"]


class Google:
    def __init__(self, customer_id):
        self.cid = customer_id
        self.h = {"Authorization": "Bearer " + token_acesso(), "Content-Type": "application/json"}
        if os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID"):
            self.h["login-customer-id"] = os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"]

    def q(self, gaql):
        linhas, pagina = [], None
        while True:
            corpo = {"query": gaql}
            if pagina:
                corpo["pageToken"] = pagina
            r = requests.post(f"{API}/customers/{self.cid}/googleAds:search", headers=self.h, json=corpo, timeout=60)
            if r.status_code != 200:
                sys.exit(f"ERRO Google Ads {r.status_code}: {r.text[:300]}")
            j = r.json()
            linhas += j.get("results", [])
            pagina = j.get("nextPageToken")
            if not pagina:
                return linhas


def reais(m):
    return int(m.get("costMicros", 0)) / 1e6


def entre(a, b):
    return f"segments.date BETWEEN '{a}' AND '{b}'"


def somar(linhas):
    m = [x["metrics"] for x in linhas]
    return {"custo": sum(reais(x) for x in m), "imp": sum(int(x.get("impressions", 0)) for x in m),
            "cli": sum(int(x.get("clicks", 0)) for x in m), "conv": sum(float(x.get("conversions", 0)) for x in m)}


def por_acao(linhas):
    c = collections.Counter()
    for r in linhas:
        c[r["segments"]["conversionActionName"]] += float(r["metrics"]["conversions"])
    return dict(c)


def termos(linhas):
    t = {}
    for r in linhas:
        nome = r["campaignSearchTermView"]["searchTerm"]
        x = r["metrics"]
        a = t.setdefault(nome, {"t": nome, "imp": 0, "cli": 0, "custo": 0.0, "conv": 0.0, "camp": set()})
        a["imp"] += int(x.get("impressions", 0))
        a["cli"] += int(x.get("clicks", 0))
        a["custo"] += reais(x)
        a["conv"] += float(x.get("conversions", 0))
        a["camp"].add("PMax" if r["campaign"]["advertisingChannelType"] == "PERFORMANCE_MAX" else "Pesquisa")
    for v in t.values():
        v["camp"] = " + ".join(sorted(v["camp"]))
    return list(t.values())


def periodo(g, dias, ontem):
    fim = ontem
    ini = fim - dt.timedelta(days=dias - 1)
    pfim = ini - dt.timedelta(days=1)
    pini = pfim - dt.timedelta(days=dias - 1)

    campos = ("campaign.name, campaign.status, campaign.advertising_channel_type, metrics.cost_micros, "
              "metrics.impressions, metrics.clicks, metrics.conversions")
    camp = g.q(f"SELECT {campos} FROM campaign WHERE {entre(ini, fim)}")
    camp_ant = g.q(f"SELECT {campos} FROM campaign WHERE {entre(pini, pfim)}")
    acao = g.q("SELECT campaign.name, segments.conversion_action_name, metrics.conversions FROM campaign "
               f"WHERE {entre(ini, fim)} AND metrics.conversions > 0")
    acao_ant = g.q("SELECT campaign.name, segments.conversion_action_name, metrics.conversions FROM campaign "
                   f"WHERE {entre(pini, pfim)} AND metrics.conversions > 0")

    cur, ant = somar(camp), somar(camp_ant)
    campanhas = []
    for r in camp:
        if int(r["metrics"].get("impressions", 0)) == 0:
            continue
        nome = r["campaign"]["name"]
        a = [x for x in camp_ant if x["campaign"]["name"] == nome]
        ant_c = somar(a) if a else {"custo": 0, "conv": 0}
        campanhas.append({
            "nome": nome,
            "tipo": "Performance Max" if r["campaign"]["advertisingChannelType"] == "PERFORMANCE_MAX" else "Pesquisa",
            "custo": reais(r["metrics"]), "imp": int(r["metrics"]["impressions"]),
            "cli": int(r["metrics"].get("clicks", 0)), "conv": float(r["metrics"].get("conversions", 0)),
            "conv_ant": ant_c["conv"], "custo_ant": ant_c["custo"]})

    tv = termos(g.q("SELECT campaign.name, campaign.advertising_channel_type, campaign_search_term_view.search_term, "
                    "metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions "
                    f"FROM campaign_search_term_view WHERE {entre(ini, fim)}"))
    com = sorted([v for v in tv if v["conv"] > 0], key=lambda v: (-v["conv"], v["custo"]))
    sem = sorted([v for v in tv if v["conv"] == 0 and v["custo"] > 0], key=lambda v: -v["custo"])[:15]

    # Temas que o Google agrupa dentro da Performance Max (uma linha sem tema = contatos nao atribuidos).
    cats, sem_tema = [], 0.0
    for c in camp:
        if c["campaign"]["advertisingChannelType"] != "PERFORMANCE_MAX" or int(c["metrics"].get("impressions", 0)) == 0:
            continue
        cid = c["campaign"]["resourceName"].split("/")[-1]
        for r in g.q("SELECT campaign_search_term_insight.category_label, metrics.clicks, metrics.impressions, "
                     "metrics.conversions FROM campaign_search_term_insight "
                     f"WHERE {entre(ini, fim)} AND campaign_search_term_insight.campaign_id = {cid}"):
            rot = r["campaignSearchTermInsight"].get("categoryLabel", "")
            x = r["metrics"]
            if rot:
                cats.append({"t": rot, "cli": int(x.get("clicks", 0)), "imp": int(x.get("impressions", 0)),
                             "conv": float(x.get("conversions", 0))})
            else:
                sem_tema += float(x.get("conversions", 0))
    cats.sort(key=lambda v: (-v["conv"], -v["cli"]))

    tipos = por_acao(acao)
    conferir(abs(sum(tipos.values()) - cur["conv"]) < 0.01,
             f"{dias}d: contatos por tipo ({sum(tipos.values())}) diferem do total ({cur['conv']})")
    conferir(abs(sum(c["conv"] for c in campanhas) - cur["conv"]) < 0.01, f"{dias}d: soma das campanhas diferente do total")
    return {"per": [str(ini), str(fim)], "ant_per": [str(pini), str(pfim)], "cur": cur, "ant": ant,
            "camps": campanhas, "tipos": tipos, "tipos_ant": por_acao(acao_ant), "conv": com, "sem": sem,
            "cats": cats, "term_conv_total": sum(v["conv"] for v in tv), "cat_total_conv": sem_tema}


def gerar_cliente(slug):
    cfg = json.loads((AQUI / "google" / slug / "config.json").read_text(encoding="utf-8"))
    g = Google(cfg["customer_id"])
    agora = dt.datetime.now(ZoneInfo("America/Sao_Paulo"))
    ontem = agora.date() - dt.timedelta(days=1)

    por = {str(d): periodo(g, d, ontem) for d in PERIODOS}
    ini30 = ontem - dt.timedelta(days=29)
    dias = {}
    for r in g.q("SELECT segments.date, metrics.cost_micros, metrics.clicks, metrics.conversions FROM customer "
                 f"WHERE {entre(ini30, ontem)}"):
        d = r["segments"]["date"]
        dias[d] = {"d": d, "custo": reais(r["metrics"]), "cli": int(r["metrics"].get("clicks", 0)),
                   "conv": float(r["metrics"].get("conversions", 0))}
    dias_lista = []
    for i in range(30):  # dia sem linha na API = dia sem dado, vira zero
        d = str(ini30 + dt.timedelta(days=i))
        dias_lista.append(dias.get(d, {"d": d, "custo": 0.0, "cli": 0, "conv": 0.0}))
    conferir(abs(sum(x["conv"] for x in dias_lista) - por["30"]["cur"]["conv"]) < 0.01,
             "contatos por dia nao fecham com o total de 30 dias")

    dados = {"periodos": por, "dias": dias_lista, "gerado": agora.strftime("%d/%m/%Y"),
             "subtitulo": cfg.get("subtitulo", "Contatos, termos de pesquisa e conversão"),
             "conta": f"{cfg['customer_id'][:3]}-{cfg['customer_id'][3:6]}-{cfg['customer_id'][6:]}"}
    html = (AQUI / "template_google.html").read_text(encoding="utf-8")
    html = html.replace("__NOME__", cfg["nome"]).replace("__DATA__", json.dumps(dados, ensure_ascii=False))
    saida = SITE / slug
    saida.mkdir(parents=True, exist_ok=True)
    (saida / "index.html").write_text(html, encoding="utf-8")
    p = por["30"]["cur"]
    print(f"OK google/{slug}: 30d R$ {p['custo']:.2f}, {p['conv']:.0f} contatos, {len(por['30']['camps'])} campanhas")


def main():
    pasta = AQUI / "google"
    slugs = sys.argv[1:] or sorted(p.name for p in pasta.iterdir() if (p / "config.json").exists())
    conferir(slugs, "nenhum cliente em google/")
    if SITE.exists() and not sys.argv[1:]:
        shutil.rmtree(SITE)
    for s in slugs:
        gerar_cliente(s)


if __name__ == "__main__":
    main()
