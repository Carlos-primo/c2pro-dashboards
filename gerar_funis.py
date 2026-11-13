"""Dashboard de acompanhamento por FUNIL (Meta Ads): uma aba por linha de produto do cliente.

Cada pasta funis/<slug>/ com um config.json vira um dashboard em site/funis/<slug>/index.html,
publicado em https://carlos-primo.github.io/c2pro-dashboards/funis/<slug>/

Uso local:   python gerar_funis.py                  (todos)
             python gerar_funis.py rodrigo-goedert  (so um)

Diferente do dashboard de vendas, aqui entram TODAS as campanhas da conta, separadas em abas pelo nome:

config.json:
  {"nome": "BJJ Protech", "subtitulo": "...", "conta_anuncio": "act_...", "criativos_gasto_minimo": 10,
   "abas": [{"id": "black", "nome": "Black Friday", "descricao": "...", "contem": ["BLACK"]},
            {"id": "erp", "nome": "ERP", "tokens": ["ERP"]}, ...]}

  "contem": trecho em qualquer lugar do nome | "tokens": palavra inteira do nome (ERP nao casa com PERPETUO).
  A primeira aba que casar leva a campanha; o que nao casar com nenhuma vai pra aba "Outras".

Resultado de cada campanha = o evento que os conjuntos dela otimizam (igual a coluna "Resultados" do
Gerenciador): compra, lead, cadastro, conversa, ThruPlay ou alcance. Dentro da aba, o dashboard separa por
tipo de resultado pra nao somar compra com lead.

Paginas: URL de destino de cada anuncio (link do criativo, sem parametros), com as metricas somadas.

Variaveis de ambiente: META_ADS_TOKEN e, opcional, SENHA_FUNIS_<SLUG> pra proteger com senha.
"""

import datetime as dt
import json
import os
import re
import shutil
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from gerar import api, criptografar, diario_seguro

AQUI = Path(__file__).resolve().parent
SITE = AQUI / "site" / "funis"
CFG = {}

PERIODOS = [7, 30]  # mais o "mes" (este mes), montado em gerar_cliente()
CAMPOS = "spend,impressions,reach,inline_link_clicks,actions,action_values,video_thruplay_watched_actions"

# A Meta devolve o mesmo evento com nomes diferentes conforme a origem; usa o primeiro que existir.
EVENTOS = {
    "visitas": ["landing_page_view", "omni_landing_page_view"],
    "checkouts": ["initiate_checkout", "omni_initiated_checkout", "offsite_conversion.fb_pixel_initiate_checkout"],
    "compras": ["purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"],
    "leads": ["lead", "offsite_conversion.fb_pixel_lead", "onsite_web_lead"],
    "cadastros": ["complete_registration", "omni_complete_registration",
                  "offsite_conversion.fb_pixel_complete_registration"],
    "conversas": ["onsite_conversion.messaging_conversation_started_7d"],
}

# Evento otimizado no conjunto -> metrica de resultado
POR_EVENTO = {"PURCHASE": "compras", "LEAD": "leads", "COMPLETE_REGISTRATION": "cadastros",
              "INITIATED_CHECKOUT": "checkouts"}
POR_OTIMIZACAO = {"THRUPLAY": "thruplay", "VIDEO_VIEWS": "thruplay", "CONVERSATIONS": "conversas",
                  "REACH": "alcance", "IMPRESSIONS": "alcance", "LEAD_GENERATION": "leads",
                  "QUALITY_LEAD": "leads", "LANDING_PAGE_VIEWS": "visitas", "LINK_CLICKS": "cliques",
                  "OFFSITE_CONVERSIONS": "compras", "VALUE": "compras"}
# Quando o conjunto nao diz (campanha antiga, sem conjunto), o tipo no nome resolve.
POR_NOME = {"VENDAS": "compras", "LEADS": "leads", "WHATSAPP": "conversas", "DISTRIBUICAO": "thruplay",
            "LEMBRETE": "alcance"}


def sem_acento(txt):
    return unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode().upper()


def tokens(nome):
    return set(re.split(r"[^A-Z0-9]+", sem_acento(nome)))


def aba_da(nome):
    n, t = sem_acento(nome), tokens(nome)
    for aba in CFG["abas"]:
        if any(sem_acento(c) in n for c in aba.get("contem", [])) or t & {sem_acento(x) for x in aba.get("tokens", [])}:
            return aba["id"]
    return "outras"


def publico_do(nome):
    t = tokens(nome)
    if t & {"PROFESSOR", "PROFESSORES", "PROFE"}:
        return "professor"
    if t & {"ALUNO", "ALUNOS"}:
        return "aluno"
    return ""


def conferir(condicao, mensagem):
    if not condicao:
        sys.exit(f"ERRO de consistencia ({CFG.get('nome')}): {mensagem}")


def insights(nivel, inicio, fim, campos, **extra):
    return api(f"{CFG['conta_anuncio']}/insights", level=nivel, fields=campos,
               time_range=json.dumps({"since": inicio.isoformat(), "until": fim.isoformat()}),
               limit=500, **extra)


def primeiro(dic, chaves):
    for k in chaves:
        if k in dic:
            return dic[k]
    return 0.0


def metricas(linha):
    acoes = {a["action_type"]: float(a["value"]) for a in linha.get("actions", []) or []}
    valores = {a["action_type"]: float(a["value"]) for a in linha.get("action_values", []) or []}
    m = {
        "gasto": float(linha.get("spend", 0)),
        "impressoes": int(linha.get("impressions", 0)),
        "alcance": int(linha.get("reach", 0)),
        "cliques": float(linha.get("inline_link_clicks", 0)),
        "thruplay": sum(float(a["value"]) for a in linha.get("video_thruplay_watched_actions", []) or []),
        "faturamento": primeiro(valores, EVENTOS["compras"]),
    }
    for k, chaves in EVENTOS.items():
        m[k] = primeiro(acoes, chaves)
    return m


def resultados_das_campanhas(todas):
    """campaign_id -> metrica de resultado, pelo evento que os conjuntos otimizam (o mais comum)."""
    conjuntos = api(f"{CFG['conta_anuncio']}/adsets", fields="campaign_id,optimization_goal,promoted_object",
                    limit=500)
    votos = {}
    for a in conjuntos:
        evento = (a.get("promoted_object") or {}).get("custom_event_type")
        r = POR_EVENTO.get(evento) or POR_OTIMIZACAO.get(a.get("optimization_goal"))
        if r:
            votos.setdefault(a["campaign_id"], Counter())[r] += 1
    saida = {}
    for c in todas:
        if c["id"] in votos:
            saida[c["id"]] = votos[c["id"]].most_common(1)[0][0]
        else:
            t = tokens(c["name"])
            saida[c["id"]] = next((r for k, r in POR_NOME.items() if k in t), "compras")
    return saida


def url_limpa(url):
    if not url:
        return ""
    p = urlsplit(url.strip())
    host = (p.netloc or "").lower().removeprefix("www.")
    if not host:
        return ""
    caminho = p.path.rstrip("/")
    return f"{host}{caminho}"


def url_do_criativo(cr):
    oss = cr.get("object_story_spec") or {}
    if oss.get("template_data", {}).get("link"):
        return url_limpa(oss["template_data"]["link"]) + " (catálogo)"
    for chave in ("link_data", "video_data", "photo_data"):
        bloco = oss.get(chave) or {}
        link = bloco.get("link") or (bloco.get("call_to_action") or {}).get("value", {}).get("link")
        if link:
            return url_limpa(link)
    links = (cr.get("asset_feed_spec") or {}).get("link_urls") or []
    for l in links:
        if l.get("website_url"):
            return url_limpa(l["website_url"])
    return url_limpa(cr.get("link_url", ""))


def detalhes_anuncios(ids):
    info = {}
    ids = list(ids)
    for i in range(0, len(ids), 50):
        filtro = [{"field": "ad.id", "operator": "IN", "value": ids[i:i + 50]}]
        for ad in api(f"{CFG['conta_anuncio']}/ads",
                      fields="effective_status,preview_shareable_link,creative{thumbnail_url,instagram_permalink_url,"
                             "object_story_spec,asset_feed_spec{link_urls},link_url}",
                      filtering=json.dumps(filtro), limit=50):
            cr = ad.get("creative", {})
            info[ad["id"]] = {"status": ad.get("effective_status", ""), "thumb": cr.get("thumbnail_url", ""),
                              "instagram": cr.get("instagram_permalink_url", ""),
                              "previa": ad.get("preview_shareable_link", ""),
                              "url": url_do_criativo(cr) or "Sem link identificado"}
    return info


def campanhas_do_periodo(inicio, fim):
    saida = {}
    for l in insights("campaign", inicio, fim, "campaign_id," + CAMPOS):
        m = metricas(l)
        if m["gasto"] > 0:
            saida[l["campaign_id"]] = m
    return saida


def ultimos(dias, ontem):
    """Ultimos N dias ate ontem, comparados com os N dias anteriores."""
    inicio = ontem - dt.timedelta(days=dias - 1)
    ant_fim = inicio - dt.timedelta(days=1)
    return inicio, ontem, ant_fim - dt.timedelta(days=dias - 1), ant_fim


def este_mes(ontem):
    """Do dia 1 do mes ate ontem, comparado com os mesmos dias do mes anterior (1 a 12/10 contra 1 a 12/11).
    Usa o mes de ontem: na rodada das 3h do dia 1 ainda mostra o mes que acabou de fechar, inteiro."""
    inicio = ontem.replace(day=1)
    ant_inicio = (inicio - dt.timedelta(days=1)).replace(day=1)
    ant_fim = min(ant_inicio + dt.timedelta(days=(ontem - inicio).days), inicio - dt.timedelta(days=1))
    return inicio, ontem, ant_inicio, ant_fim


def periodo(inicio, fim, ant_inicio, ant_fim):
    dias = f"{inicio}..{fim}"

    camp = campanhas_do_periodo(inicio, fim)
    total = insights("account", inicio, fim, CAMPOS)
    total_gasto = float(total[0]["spend"]) if total else 0.0
    conferir(abs(sum(m["gasto"] for m in camp.values()) - total_gasto) < 0.05,
             f"{dias}d: soma das campanhas difere do total da conta")

    anuncios = []
    for l in insights("ad", inicio, fim, "ad_id,ad_name,campaign_id," + CAMPOS):
        m = metricas(l)
        if m["gasto"] > 0:
            anuncios.append({"id": l["ad_id"], "nome": l["ad_name"], "campanha_id": l["campaign_id"], **m})
    conferir(abs(sum(a["gasto"] for a in anuncios) - total_gasto) < 0.05,
             f"{dias}d: soma dos anuncios difere do total da conta")

    return {"inicio": inicio.isoformat(), "fim": fim.isoformat(),
            "ant_inicio": ant_inicio.isoformat(), "ant_fim": ant_fim.isoformat(),
            "campanhas": camp, "anterior": campanhas_do_periodo(ant_inicio, ant_fim), "anuncios": anuncios}


def gerar_cliente(slug):
    CFG.clear()
    CFG.update(json.loads((AQUI / "funis" / slug / "config.json").read_text(encoding="utf-8")))
    saida = SITE / slug

    conta = api(CFG["conta_anuncio"], fields="name,currency,timezone_name")
    fuso = ZoneInfo(conta.get("timezone_name") or "America/Sao_Paulo")
    agora = dt.datetime.now(fuso)
    ontem = agora.date() - dt.timedelta(days=1)

    todas = api(f"{CFG['conta_anuncio']}/campaigns", fields="name,effective_status", limit=500)
    resultado = resultados_das_campanhas(todas)
    campanhas = {c["id"]: {"id": c["id"], "nome": c["name"], "status": c["effective_status"],
                           "aba": aba_da(c["name"]), "publico": publico_do(c["name"]),
                           "resultado": resultado[c["id"]]} for c in todas}

    periodos = {str(d): periodo(*ultimos(d, ontem)) for d in PERIODOS}
    periodos["mes"] = periodo(*este_mes(ontem))

    ids_ads = {a["id"] for p in periodos.values() for a in p["anuncios"]}
    det = detalhes_anuncios(ids_ads)
    for p in periodos.values():
        for a in p["anuncios"]:
            a.update(det.get(a["id"], {"status": "", "thumb": "", "instagram": "", "previa": "",
                                       "url": "Sem link identificado"}))

    # Dia a dia (30 dias) por campanha, pra montar o grafico de cada aba no navegador.
    inicio30 = ontem - dt.timedelta(days=29)
    diario = {}
    for l in diario_seguro(lambda c, i, f, **e: insights("campaign", i, f, c, **e),
                           "campaign_id,spend,actions,action_values,video_thruplay_watched_actions,reach",
                           inicio30, ontem):
        m = metricas(l)
        if m["gasto"] > 0:
            diario.setdefault(l["campaign_id"], {})[l["date_start"]] = {
                k: m[k] for k in ("gasto", "compras", "faturamento", "leads", "cadastros", "conversas", "thruplay",
                                  "alcance", "visitas", "cliques", "checkouts")}
    soma_diaria = sum(d["gasto"] for c in diario.values() for d in c.values())
    soma_30 = sum(m["gasto"] for m in periodos["30"]["campanhas"].values())
    conferir(abs(soma_diaria - soma_30) < 0.05, "serie diaria nao bate com o total de 30 dias")

    usadas = {cid for p in periodos.values() for cid in list(p["campanhas"]) + list(p["anterior"])} | set(diario)
    for cid in usadas - set(campanhas):  # campanha apagada: ainda aparece com o que gastou
        campanhas[cid] = {"id": cid, "nome": cid, "status": "DELETED", "aba": "outras", "publico": "",
                          "resultado": "compras"}

    abas = [{k: a.get(k, "") for k in ("id", "nome", "descricao")} for a in CFG["abas"]]
    abas.append({"id": "outras", "nome": "Outras", "descricao": "Campanhas que não entram em nenhum funil acima."})

    dados = {
        "nome": CFG["nome"], "subtitulo": CFG.get("subtitulo", ""), "conta": conta.get("name", ""),
        "moeda": conta.get("currency", "BRL"), "gerado_em": agora.isoformat(timespec="minutes"),
        "gasto_minimo_criativo": CFG.get("criativos_gasto_minimo", 10),
        "abas": abas,
        "campanhas": {cid: campanhas[cid] for cid in usadas},
        "periodos": periodos,
        "dias": [(inicio30 + dt.timedelta(days=n)).isoformat() for n in range(30)],
        "diario": diario,
    }

    json_dados = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    senha = os.environ.get(f"SENHA_FUNIS_{slug.upper().replace('-', '_')}", "")
    payload = criptografar(json_dados, senha) if senha else {"enc": False, "dados": dados}

    saida.mkdir(parents=True, exist_ok=True)
    html = (AQUI / "template_funis.html").read_text(encoding="utf-8")
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    (saida / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(AQUI / "logo.png", saida / "logo.png")

    por_aba = Counter()
    for cid, m in periodos["30"]["campanhas"].items():
        por_aba[campanhas[cid]["aba"]] += m["gasto"]
    resumo = ", ".join(f"{k} R$ {v:.0f}" for k, v in por_aba.most_common())
    print(f"OK funis/{slug}: 30d {resumo} | {len(ids_ads)} anuncios | "
          f"{'com senha' if senha else 'link aberto, sem senha'}")


def main():
    todos = sorted(p.parent.name for p in (AQUI / "funis").glob("*/config.json"))
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
            print(f"::error::{slug} ficou de fora: {erro}")
    if falhas and sys.argv[1:]:
        sys.exit(1)


if __name__ == "__main__":
    main()
