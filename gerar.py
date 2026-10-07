"""Gera os dashboards de distribuicao (Meta Ads) direto da API da Meta, sem Claude no meio.

Cada pasta clientes/<slug>/ com um config.json vira um dashboard em site/<slug>/index.html,
publicado em https://carlos-primo.github.io/c2pro-dashboards/<slug>/

Uso local:   python gerar.py            (todos os clientes)
             python gerar.py c2pro      (so um)
No GitHub:   roda sozinho todo dia as 2h (ver .github/workflows/atualizar.yml)

config.json opcional: "periodo_fixo": {"inicio": "AAAA-MM-DD", "fim": "AAAA-MM-DD", "rotulo": "..."}
troca os ultimos 7/30 dias por essa janela (campanha que ja acabou ou com data marcada).

Variaveis de ambiente:
  META_ADS_TOKEN   token da Meta (local: cai no .env da skill meta-ads do workspace ao lado)
  SENHA_<SLUG>     opcional (ex: SENHA_C2PRO): se definida, o dashboard daquele cliente pede senha
"""

import base64
import datetime as dt
import json
import os
import shutil
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

AQUI = Path(__file__).resolve().parent
SITE = AQUI / "site"
ENV_SKILL = AQUI.parent / "Claude Code" / ".claude" / "skills" / "meta-ads" / ".env"
BASE = "https://graph.facebook.com/v26.0"
CFG = {}  # config do cliente sendo gerado, preenchida em gerar_cliente()

CAMPOS_METRICAS = ",".join([
    "spend", "impressions", "reach", "actions",
    "video_p25_watched_actions", "video_p50_watched_actions", "video_p75_watched_actions",
    "video_p100_watched_actions", "video_thruplay_watched_actions", "video_avg_time_watched_actions",
])


def carregar_token():
    token = os.environ.get("META_ADS_TOKEN")
    if token:
        return token.strip().strip('"')
    if ENV_SKILL.exists():
        for linha in ENV_SKILL.read_text(encoding="utf-8").splitlines():
            if linha.startswith("META_ADS_TOKEN="):
                return linha.split("=", 1)[1].strip().strip('"')
    sys.exit("ERRO: defina META_ADS_TOKEN (ou configure a skill meta-ads).")


TOKEN = carregar_token()


def api(caminho, **params):
    """GET na Graph API com retry simples e paginacao."""
    params["access_token"] = TOKEN
    url, itens = f"{BASE}/{caminho}", []
    while url:
        for tentativa in range(4):
            r = requests.get(url, params=params, timeout=60)
            if r.status_code < 500 and r.status_code != 429:
                break
            time.sleep(5 * (tentativa + 1))
        corpo = r.json()
        if "error" in corpo:
            sys.exit(f"ERRO da Meta em {caminho}: {corpo['error'].get('message')}")
        if "data" not in corpo:
            return corpo
        itens += corpo["data"]
        url, params = corpo.get("paging", {}).get("next"), {}
    return itens


# Regra da agencia: dashboard de distribuicao so mostra campanhas com DISTRIBUICAO no nome.
# Fixo no codigo de proposito, nao e configuravel por cliente.
FILTRO = "DISTRIBUICAO"


def filtro_campanha():
    return [{"field": "campaign.name", "operator": "CONTAIN", "value": FILTRO}]


def eh_distribuicao(nome_campanha):
    return FILTRO in (nome_campanha or "").upper()


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
        filtering=json.dumps(filtro_campanha()),
        limit=500,
        **extra,
    )


def metricas(linha):
    """Normaliza uma linha de insights no formato que o dashboard usa."""
    acoes = {a["action_type"]: float(a["value"]) for a in linha.get("actions", [])}
    video = lambda campo: sum(float(a["value"]) for a in linha.get(campo, []))
    return {
        "gasto": float(linha.get("spend", 0)),
        "impressoes": int(linha.get("impressions", 0)),
        "alcance": int(linha.get("reach", 0)),
        "views3s": acoes.get("video_view", 0),
        "p25": video("video_p25_watched_actions"),
        "p50": video("video_p50_watched_actions"),
        "p75": video("video_p75_watched_actions"),
        "p100": video("video_p100_watched_actions"),
        "thruplay": video("video_thruplay_watched_actions"),
        "tempo_medio": video("video_avg_time_watched_actions"),
        "engajamento": acoes.get("post_engagement", 0),
    }


def conta_total(inicio, fim):
    linhas = insights("account", inicio, fim, CAMPOS_METRICAS)
    return metricas(linhas[0]) if linhas else metricas({})


def periodo(dias, ontem):
    return periodo_entre(ontem - dt.timedelta(days=dias - 1), ontem)


def periodo_fixo(ontem):
    """Janela fechada do config ("periodo_fixo": {"inicio", "fim"}) no lugar dos ultimos 7/30 dias.
    Serve pra campanha que ja acabou: o dashboard mostra so as datas dela."""
    pf = CFG.get("periodo_fixo")
    if not pf:
        return None
    inicio = dt.date.fromisoformat(pf["inicio"])
    fim = min(dt.date.fromisoformat(pf["fim"]), ontem)
    conferir(inicio <= fim, f"periodo_fixo invalido: {pf}")
    return inicio, fim


def periodo_entre(inicio, fim):
    dias = (fim - inicio).days + 1
    ant_fim = inicio - dt.timedelta(days=1)
    ant_inicio = ant_fim - dt.timedelta(days=dias - 1)

    status_camp = {c["id"]: c["effective_status"] for c in api(
        f"{CFG['conta_anuncio']}/campaigns", fields="effective_status", limit=500)}

    campanhas = []
    for l in insights("campaign", inicio, fim, "campaign_id,campaign_name," + CAMPOS_METRICAS):
        conferir(eh_distribuicao(l["campaign_name"]), f"campanha fora do filtro: {l['campaign_name']}")
        m = metricas(l)
        if m["gasto"] > 0:
            campanhas.append({"id": l["campaign_id"], "nome": l["campaign_name"],
                              "status": status_camp.get(l["campaign_id"], ""), **m})
    campanhas.sort(key=lambda c: -c["gasto"])

    anuncios = []
    for l in insights("ad", inicio, fim, "ad_id,ad_name,campaign_name," + CAMPOS_METRICAS):
        conferir(eh_distribuicao(l["campaign_name"]), f"anuncio de campanha fora do filtro: {l['campaign_name']}")
        m = metricas(l)
        if m["gasto"] > 0:
            anuncios.append({"id": l["ad_id"], "nome": l["ad_name"],
                             "campanha": l["campaign_name"], **m})
    detalhes_anuncios(anuncios)

    atual = conta_total(inicio, fim)
    soma = sum(c["gasto"] for c in campanhas)
    conferir(abs(soma - atual["gasto"]) < 0.05,
             f"total {dias}d (R$ {atual['gasto']:.2f}) difere da soma das campanhas DISTRIBUICAO (R$ {soma:.2f})")

    return {
        "inicio": inicio.isoformat(), "fim": fim.isoformat(),
        "ant_inicio": ant_inicio.isoformat(), "ant_fim": ant_fim.isoformat(),
        "atual": atual,
        "anterior": conta_total(ant_inicio, ant_fim),
        "campanhas": campanhas,
        "anuncios": anuncios,
    }


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


def serie_diaria(inicio, fim):
    por_dia = {l["date_start"]: metricas(l) for l in insights(
        "account", inicio, fim, CAMPOS_METRICAS, time_increment=1)}
    dias = []
    for n in range((fim - inicio).days + 1):
        d = (inicio + dt.timedelta(days=n)).isoformat()
        m = por_dia.get(d, metricas({}))
        dias.append({"data": d, "gasto": m["gasto"], "impressoes": m["impressoes"],
                     "views3s": m["views3s"], "thruplay": m["thruplay"]})
    return dias


def criptografar(texto, senha):
    """AES-GCM com chave PBKDF2, no mesmo formato que o WebCrypto do navegador le."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    sal, iv = os.urandom(16), os.urandom(12)
    chave = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=sal,
                       iterations=250_000).derive(senha.encode())
    ct = AESGCM(chave).encrypt(iv, texto.encode(), None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"enc": True, "sal": b64(sal), "iv": b64(iv), "ct": b64(ct), "iter": 250_000}


def gerar_cliente(slug):
    CFG.clear()
    CFG.update(json.loads((AQUI / "clientes" / slug / "config.json").read_text(encoding="utf-8")))
    saida = SITE / slug

    conta = api(CFG["conta_anuncio"], fields="name,currency,timezone_name")
    fuso = ZoneInfo(conta.get("timezone_name") or "America/Sao_Paulo")
    agora = dt.datetime.now(fuso)
    ontem = agora.date() - dt.timedelta(days=1)

    fixo = periodo_fixo(ontem)
    if fixo:
        periodos = {"fixo": {**periodo_entre(*fixo), "rotulo": CFG["periodo_fixo"].get("rotulo", "")}}
        diario, chave = serie_diaria(*fixo), "fixo"
    else:
        periodos = {"7": periodo(7, ontem), "30": periodo(30, ontem)}
        diario, chave = serie_diaria(ontem - dt.timedelta(days=29), ontem), "30"

    dados = {
        "nome": CFG["nome"],
        "subtitulo": CFG.get("subtitulo", ""),
        "conta": conta.get("name", ""),
        "moeda": conta.get("currency", "BRL"),
        "filtro": FILTRO,
        "gerado_em": agora.isoformat(timespec="minutes"),
        "gasto_minimo_criativo": CFG["criativos_gasto_minimo"],
        "fixo": bool(fixo),
        "periodos": periodos,
        "diario": diario,
    }
    soma_diaria = sum(d["gasto"] for d in diario)
    conferir(abs(soma_diaria - periodos[chave]["atual"]["gasto"]) < 0.05,
             "serie diaria nao bate com o total do periodo")

    json_dados = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    senha = os.environ.get(f"SENHA_{slug.upper().replace('-', '_')}", "")
    payload = criptografar(json_dados, senha) if senha else {"enc": False, "dados": dados}

    saida.mkdir(parents=True, exist_ok=True)
    html = (AQUI / "template.html").read_text(encoding="utf-8")
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    (saida / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(AQUI / "logo.png", saida / "logo.png")

    p = periodos[chave]
    janela = f"{p['inicio']} a {p['fim']}" if fixo else "30d"
    print(f"OK {slug}: {janela} R$ {p['atual']['gasto']:.2f}, "
          f"{len(p['campanhas'])} campanhas, {len(p['anuncios'])} anuncios | "
          f"{'com senha' if senha else 'link aberto, sem senha'}")


def main():
    todos = sorted(p.parent.name for p in (AQUI / "clientes").glob("*/config.json"))
    SITE.mkdir(exist_ok=True)
    # Raiz do site vazia de proposito: cada cliente so conhece o proprio link.
    (SITE / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
        '<title>C2Pro</title><body style="background:#05070A"></body>', encoding="utf-8")
    for slug in sys.argv[1:] or todos:
        gerar_cliente(slug)


if __name__ == "__main__":
    main()
