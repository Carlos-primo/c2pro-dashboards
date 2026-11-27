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

Opcional: "google_customer_id": "8756808672" cria a aba Google Ads (campanhas, paginas e dia a dia).
Opcional: "periodos": [1, 3, 7, 14, 30]  troca os botoes de periodo (padrao 7, 30 e "mes"; 30 e obrigatorio,
          e a base do dia a dia). "mes" = este mes. 1 aparece como "Ontem".
Opcional: "contas": [{"id": "act_...", "nome": "..."}]  junta varias contas de anuncio num dashboard so (no lugar
          de "conta_anuncio"); cada campanha mostra de qual conta e (U-TRAX, 24/11/2026).
Opcional: "quebras": true  idade, genero e posicionamento em cada aba e na visao geral, mais o alerta dos
          conjuntos ativos (idade aberta ate 65+, acima de "idade_maxima" ou com Advantage+ de publico ligado).
Opcional: "gargalos": true  troca o funil de cada aba pelo funil com gargalos: cada taxa de passagem (CTR, carregamento,
          conversao, checkout, compra, hook, retencao) abaixo da referencia fica vermelha, a pior vira o "principal
          gargalo" e a visao geral ganha o quadro "Gargalos por funil". Referencias no template (REF_PADRAO); para trocar,
          {"ctr": 1.2, "carregamento": 75, ...} no lugar do true (U-TRAX, 27/11/2026).
Opcional: "aba_inicial": "<id da aba>"  o dashboard abre nessa aba em vez da visao geral.
          0 nos periodos = "Hoje": parcial, da meia-noite ate a hora da rodada, comparado com ontem inteiro.

Tambem e o motor do clientes/<slug> com "modelo": "acompanhamento" (Ingrid Villarraso, 18/11/2026): o gerar.py
chama gerar_cliente(slug, pasta="clientes"), sai em site/<slug>/ (link de distribuicao de sempre), senha SENHA_<SLUG>.

Tambem e o motor do vendas/<slug> com "modelo": "acompanhamento" no config (Ordus, 17/11/2026): o gerar_vendas.py
chama gerar_cliente(slug, pasta="vendas") e o link de vendas continua o mesmo, com senha SENHA_VENDAS_<SLUG>.

Variaveis de ambiente: META_ADS_TOKEN, as GOOGLE_ADS_* (se tiver Google) e, opcional, SENHA_FUNIS_<SLUG>.
"""

import datetime as dt
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from gerar import api, criptografar, diario_seguro

AQUI = Path(__file__).resolve().parent
SITE = AQUI / "site" / "funis"
CFG = {}

PERIODOS = [7, 30, "mes"]  # padrao; o config pode trocar com "periodos"
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
    "views": ["video_view"],              # views de 3s (hook rate = views / impressoes)
    "engajamento": ["post_engagement"],
}

# Evento otimizado no conjunto -> metrica de resultado
POR_EVENTO = {"PURCHASE": "compras", "LEAD": "leads", "COMPLETE_REGISTRATION": "cadastros",
              "INITIATED_CHECKOUT": "checkouts",
              # CONTACT no pixel com link de grupo de WhatsApp (Ingrid, GRUPO_VIP_BLACK_26): o pixel nao ve a entrada
              # no grupo, entao o numero que conta e o clique no link do grupo.
              "CONTACT": "cliques"}
POR_OTIMIZACAO = {"THRUPLAY": "thruplay", "VIDEO_VIEWS": "thruplay", "CONVERSATIONS": "conversas",
                  "REACH": "alcance", "IMPRESSIONS": "alcance", "LEAD_GENERATION": "leads",
                  "QUALITY_LEAD": "leads", "LANDING_PAGE_VIEWS": "visitas", "LINK_CLICKS": "cliques",
                  "OFFSITE_CONVERSIONS": "compras", "VALUE": "compras",
                  # Visita ao perfil e engajamento: a API nao devolve as visitas ao perfil, entao o resultado e o
                  # engajamento (antes caia em "compras" e aparecia 0). U-TRAX, 24/11/2026.
                  "POST_ENGAGEMENT": "engajamento", "PROFILE_VISIT": "engajamento",
                  "VISIT_INSTAGRAM_PROFILE": "engajamento", "PROFILE_AND_PAGE_ENGAGEMENT": "engajamento",
                  "PAGE_LIKES": "engajamento"}
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
    for chave in ("link_data", "video_data", "photo_data"):
        cta = (oss.get(chave) or {}).get("call_to_action") or {}
        if cta.get("type") == "WHATSAPP_MESSAGE" or (cta.get("value") or {}).get("app_destination") == "WHATSAPP":
            return "WhatsApp (conversa direto pelo anúncio)"
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


def hoje(dia):
    """Hoje (parcial, ate a hora da rodada) comparado com ontem inteiro."""
    ontem = dia - dt.timedelta(days=1)
    return dia, dia, ontem, ontem


def periodo(inicio, fim, ant_inicio, ant_fim):
    dias = f"{inicio}..{fim}"
    # Hoje ainda esta gastando: entre a chamada da conta e a dos anuncios o valor pode andar alguns centavos.
    tol = 1.0 if fim == CFG.get("_hoje") else 0.05

    # A Meta ainda acerta o gasto de ontem ao longo do dia: entre uma chamada e outra o total anda alguns centavos
    # (U-TRAX, 24/11/2026, falhou 4 vezes no mesmo dia). Busca de novo ate 3 vezes antes de abortar.
    for tentativa in range(3):
        camp = campanhas_do_periodo(inicio, fim)
        total = insights("account", inicio, fim, CAMPOS)
        total_gasto = float(total[0]["spend"]) if total else 0.0
        anuncios = []
        for l in insights("ad", inicio, fim, "ad_id,ad_name,campaign_id," + CAMPOS):
            m = metricas(l)
            if m["gasto"] > 0:
                anuncios.append({"id": l["ad_id"], "nome": l["ad_name"], "campanha_id": l["campaign_id"], **m})
        soma_c, soma_a = sum(m["gasto"] for m in camp.values()), sum(a["gasto"] for a in anuncios)
        if abs(soma_c - total_gasto) < tol and abs(soma_a - total_gasto) < tol:
            break
        if tentativa < 2:
            print(f"  aviso: {dias} {CFG['conta_anuncio']} nao bateu (campanhas {soma_c:.2f}, anuncios {soma_a:.2f}, "
                  f"conta {total_gasto:.2f}); buscando de novo em 30s")
            time.sleep(30)
    conferir(abs(soma_c - total_gasto) < tol,
             f"{dias}d: soma das campanhas ({soma_c:.2f}) difere do total da conta {CFG['conta_anuncio']} ({total_gasto:.2f})")
    conferir(abs(soma_a - total_gasto) < tol,
             f"{dias}d: soma dos anuncios ({soma_a:.2f}) difere do total da conta {CFG['conta_anuncio']} ({total_gasto:.2f})")

    return {"inicio": inicio.isoformat(), "fim": fim.isoformat(),
            "ant_inicio": ant_inicio.isoformat(), "ant_fim": ant_fim.isoformat(),
            "campanhas": camp, "anterior": campanhas_do_periodo(ant_inicio, ant_fim), "anuncios": anuncios}


def vazio_google(m):
    """Metricas do Google no mesmo formato das da Meta. Conversao do Google = compra (Purchase - GTM)."""
    base = {k: 0.0 for k in ("gasto", "impressoes", "alcance", "cliques", "thruplay", "faturamento", "visitas",
                             "checkouts", "compras", "leads", "cadastros", "conversas")}
    base.update({"gasto": int(m.get("costMicros", 0)) / 1e6, "impressoes": int(m.get("impressions", 0)),
                 "cliques": float(m.get("clicks", 0)), "compras": float(m.get("conversions", 0)),
                 "faturamento": float(m.get("conversionsValue", 0))})
    return base


def juntar_google(periodos, campanhas, diario, dias):
    """Aba "Google Ads": campanhas, paginas (landing_page_view) e dia a dia da conta Google do cliente.
    Se a credencial falhar, o dashboard sai so com a Meta (aviso no log), como no gerar_funil.py."""
    import gerar_google
    try:
        g = gerar_google.Google(CFG["google_customer_id"])
        entre = gerar_google.entre
        campos = "campaign.id, campaign.name, campaign.status, metrics.cost_micros, metrics.impressions, "                  "metrics.clicks, metrics.conversions, metrics.conversions_value"
        def camp(a, b):
            saida = {}
            for r in g.q(f"SELECT {campos} FROM campaign WHERE {entre(a, b)}"):
                m = vazio_google(r["metrics"])
                if m["gasto"] > 0:
                    cid = "g" + r["campaign"]["id"]
                    saida[cid] = m
                    campanhas[cid] = {"id": cid, "nome": r["campaign"]["name"], "status": r["campaign"]["status"],
                                      "aba": "google", "publico": "", "resultado": "compras", "plataforma": "google"}
            return saida
        novos = {}
        for k, p in periodos.items():
            atual, ant = camp(p["inicio"], p["fim"]), camp(p["ant_inicio"], p["ant_fim"])
            paginas = {}
            for r in g.q("SELECT landing_page_view.unexpanded_final_url, metrics.cost_micros, metrics.impressions, "
                         "metrics.clicks, metrics.conversions, metrics.conversions_value FROM landing_page_view "
                         f"WHERE {entre(p['inicio'], p['fim'])}"):
                m = vazio_google(r["metrics"])
                if m["gasto"] > 0:
                    u = url_limpa(r["landingPageView"].get("unexpandedFinalUrl", "")) or "Sem link identificado"
                    t = paginas.setdefault(u, {"url": u, **{x: 0.0 for x in m}})
                    for x in m:
                        t[x] += m[x]
            novos[k] = (atual, ant, list(paginas.values()))
        d = {}
        for r in g.q(f"SELECT campaign.id, segments.date, metrics.cost_micros, metrics.impressions, metrics.clicks, "
                     f"metrics.conversions, metrics.conversions_value FROM campaign WHERE {entre(dias[0], dias[-1])}"):
            m = vazio_google(r["metrics"])
            if m["gasto"] > 0:
                d.setdefault("g" + r["campaign"]["id"], {})[r["segments"]["date"]] = m
    except SystemExit as erro:
        print(f"::warning::{CFG['nome']} saiu sem o Google Ads: {erro}")
        return False
    for k, (atual, ant, paginas) in novos.items():
        periodos[k]["campanhas"].update(atual)
        periodos[k]["anterior"].update(ant)
        periodos[k]["paginas_google"] = paginas
    diario.update(d)
    return True


# Quebras de publico (config "quebras": true): idade, genero e posicionamento por campanha, em cada periodo.
QUEBRAS = {"idade": "age", "genero": "gender", "posicao": "publisher_platform,platform_position"}
CHAVES_QUEBRA = ("gasto", "impressoes", "alcance", "cliques", "leads", "conversas", "compras", "cadastros", "visitas",
                 "checkouts", "thruplay", "faturamento", "views", "engajamento")


def quebras(inicio, fim):
    """Linhas por campanha e faixa. O alcance nao vai (nao soma entre faixas). Sem conferencia de total: a Meta
    pode deixar de fora uma parte pequena do gasto nas quebras (ela avisa isso no Gerenciador)."""
    saida = {}
    for nome, bd in QUEBRAS.items():
        linhas = []
        for l in insights("campaign", inicio, fim, "campaign_id," + CAMPOS, breakdowns=bd):
            m = metricas(l)
            if m["gasto"] > 0 or m["impressoes"]:
                faixa = "|".join(l.get(x, "") for x in bd.split(","))
                linhas.append({"c": l["campaign_id"], "f": faixa, **{k: round(m[k], 2) for k in CHAVES_QUEBRA}})
        saida[nome] = linhas
    # Idade por anuncio (cartao de cada criativo). So as chaves com valor, pra pagina nao crescer demais.
    saida["idade_ad"] = []
    for l in insights("ad", inicio, fim, "ad_id," + CAMPOS, breakdowns="age"):
        m = metricas(l)
        if m["gasto"] > 0:
            saida["idade_ad"].append({"a": l["ad_id"], "f": l.get("age", ""),
                                      **{k: round(m[k], 2) for k in CHAVES_QUEBRA if m[k]}})
    return saida


def conjuntos_ativos(nome_conta):
    """Idade e Advantage+ de publico de cada conjunto ativo agora (o alerta da visao geral).
    age_max 65 na Meta = "65 ou mais"; com o Advantage+ de publico ligado, a idade maxima vira so sugestao."""
    saida = []
    for a in api(f"{CFG['conta_anuncio']}/adsets",
                 fields="name,campaign_id,effective_status,targeting{age_min,age_max,targeting_automation}",
                 effective_status=json.dumps(["ACTIVE"]), limit=500):
        t = a.get("targeting") or {}
        saida.append({"nome": a["name"], "campanha_id": a["campaign_id"], "conta": nome_conta,
                      "idade_min": t.get("age_min", 18), "idade_max": t.get("age_max", 65),
                      "advantage": (t.get("targeting_automation") or {}).get("advantage_audience", 0) == 1})
    return saida


def coletar_conta(agora, ontem, nome_conta):
    """Campanhas, periodos, anuncios e dia a dia da conta em CFG["conta_anuncio"]."""
    todas = api(f"{CFG['conta_anuncio']}/campaigns", fields="name,effective_status", limit=500)
    resultado = resultados_das_campanhas(todas)
    campanhas = {c["id"]: {"id": c["id"], "nome": c["name"], "status": c["effective_status"],
                           "aba": aba_da(c["name"]), "publico": publico_do(c["name"]),
                           "resultado": resultado[c["id"]], **({"conta": nome_conta} if nome_conta else {})}
                 for c in todas}

    lista = CFG.get("periodos", PERIODOS)
    conferir(30 in lista, "periodos precisa ter 30 (base do dia a dia)")
    periodos = {str(d): periodo(*(este_mes(ontem) if d == "mes" else hoje(agora.date()) if d == 0
                                  else ultimos(int(d), ontem))) for d in lista}
    if CFG.get("quebras"):
        for p in periodos.values():
            p["quebras"] = quebras(dt.date.fromisoformat(p["inicio"]), dt.date.fromisoformat(p["fim"]))

    ids_ads = {a["id"] for p in periodos.values() for a in p["anuncios"]}
    det = detalhes_anuncios(ids_ads)
    for p in periodos.values():
        for a in p["anuncios"]:
            a.update(det.get(a["id"], {"status": "", "thumb": "", "instagram": "", "previa": "",
                                       "url": "Sem link identificado"}))

    # Dia a dia (30 dias) por campanha, pra montar o grafico de cada aba no navegador.
    inicio30 = ontem - dt.timedelta(days=29)
    soma_30 = sum(m["gasto"] for m in periodos["30"]["campanhas"].values())
    for tentativa in range(3):  # mesmo motivo do periodo(): o gasto de ontem ainda anda alguns centavos
        diario = {}
        for l in diario_seguro(lambda c, i, f, **e: insights("campaign", i, f, c, **e),
                               "campaign_id,spend,inline_link_clicks,actions,action_values,video_thruplay_watched_actions,reach",
                               inicio30, ontem):
            m = metricas(l)
            if m["gasto"] > 0:
                diario.setdefault(l["campaign_id"], {})[l["date_start"]] = {
                    k: m[k] for k in ("gasto", "compras", "faturamento", "leads", "cadastros", "conversas", "thruplay",
                                      "alcance", "visitas", "cliques", "checkouts", "views", "engajamento")}
        soma_diaria = sum(d["gasto"] for c in diario.values() for d in c.values())
        if abs(soma_diaria - soma_30) < 0.05:
            break
        if tentativa < 2:
            print(f"  aviso: serie diaria {CFG['conta_anuncio']} ({soma_diaria:.2f}) nao bateu com 30d ({soma_30:.2f}); "
                  "buscando de novo em 30s")
            time.sleep(30)
    conferir(abs(soma_diaria - soma_30) < 0.05,
             f"serie diaria ({soma_diaria:.2f}) nao bate com o total de 30 dias ({soma_30:.2f})")
    conjuntos = conjuntos_ativos(nome_conta) if CFG.get("quebras") else []
    for cj in conjuntos:
        cj["campanha"] = campanhas.get(cj["campanha_id"], {}).get("nome", "")
    return campanhas, periodos, ids_ads, diario, conjuntos


def gerar_cliente(slug, pasta="funis"):
    CFG.clear()
    CFG.update(json.loads((AQUI / pasta / slug / "config.json").read_text(encoding="utf-8")))
    saida = AQUI / "site" / slug if pasta == "clientes" else AQUI / "site" / pasta / slug

    # "contas": [{"id": "act_...", "nome": "..."}] junta mais de uma conta de anuncio no mesmo dashboard
    # (U-TRAX, 24/11/2026). Sem "contas", vale o "conta_anuncio" de sempre.
    contas = [(c["id"], c.get("nome", "")) for c in CFG.get("contas", [])] or [(CFG["conta_anuncio"], "")]
    CFG["conta_anuncio"] = contas[0][0]
    conta = api(CFG["conta_anuncio"], fields="name,currency,timezone_name")
    fuso = ZoneInfo(conta.get("timezone_name") or "America/Sao_Paulo")
    agora = dt.datetime.now(fuso)
    ontem = agora.date() - dt.timedelta(days=1)
    CFG["_hoje"] = agora.date()

    campanhas, periodos, ids_ads, diario, conjuntos = {}, {}, set(), {}, []
    for act, nome_conta in contas:
        CFG["conta_anuncio"] = act
        c, p, ids, d, cj = coletar_conta(agora, ontem, nome_conta or (conta.get("name", "") if len(contas) > 1 else ""))
        campanhas.update(c)
        ids_ads |= ids
        diario.update(d)
        conjuntos += cj
        for k, per in p.items():
            if k not in periodos:
                periodos[k] = per
                continue
            periodos[k]["campanhas"].update(per["campanhas"])
            periodos[k]["anterior"].update(per["anterior"])
            periodos[k]["anuncios"] += per["anuncios"]
            for q, linhas in per.get("quebras", {}).items():
                periodos[k]["quebras"][q] += linhas
    CFG["conta_anuncio"] = contas[0][0]

    inicio30 = ontem - dt.timedelta(days=29)
    dias30 = [(inicio30 + dt.timedelta(days=n)).isoformat() for n in range(30)]
    tem_google = bool(CFG.get("google_customer_id")) and juntar_google(periodos, campanhas, diario, dias30)

    usadas = {cid for p in periodos.values() for cid in list(p["campanhas"]) + list(p["anterior"])} | set(diario)
    for cid in usadas - set(campanhas):  # campanha apagada: ainda aparece com o que gastou
        campanhas[cid] = {"id": cid, "nome": cid, "status": "DELETED", "aba": "outras", "publico": "",
                          "resultado": "compras"}

    abas = [{k: a.get(k, "") for k in ("id", "nome", "descricao")} for a in CFG["abas"]]
    if tem_google:
        abas.append({"id": "google", "nome": "Google Ads",
                     "descricao": "Campanhas do Google Ads (pesquisa, geração de demanda e PMax). Conversão = compra "
                                  "registrada pela tag do Google; não tem visita à página, então a conversão é "
                                  "compras ÷ cliques."})
    abas.append({"id": "outras", "nome": "Outras", "descricao": "Campanhas que não entram em nenhum funil acima."})

    dados = {
        "nome": CFG["nome"], "subtitulo": CFG.get("subtitulo", ""),
        "conta": " + ".join(n for _, n in contas) if len(contas) > 1 else conta.get("name", ""),
        **({"conjuntos": conjuntos, "idade_maxima": CFG.get("idade_maxima", 65)} if CFG.get("quebras") else {}),
        **({"gargalos": CFG["gargalos"]} if CFG.get("gargalos") else {}),
        "moeda": conta.get("currency", "BRL"), "gerado_em": agora.isoformat(timespec="minutes"),
        "gasto_minimo_criativo": CFG.get("criativos_gasto_minimo", 10),
        "abas": abas, "aba_inicial": CFG.get("aba_inicial", "geral"),
        "periodo_inicial": str(CFG.get("periodo_inicial", "7")),
        "campanhas": {cid: campanhas[cid] for cid in usadas},
        "periodos": periodos,
        "dias": dias30,
        "diario": diario,
    }

    json_dados = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    prefixo = {"vendas": "SENHA_VENDAS_", "clientes": "SENHA_"}.get(pasta, "SENHA_FUNIS_")
    senha = os.environ.get(prefixo + slug.upper().replace('-', '_'), "")
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
    print(f"OK {pasta}/{slug}: 30d {resumo} | {len(ids_ads)} anuncios | "
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
