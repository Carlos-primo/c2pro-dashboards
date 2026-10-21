"""Gera os dashboards da BLACK FRIDAY (Meta Ads), separados por conta de anuncio.

Cada pasta black/<slug>/ com um config.json vira site/black/<slug>/index.html, publicado em
https://carlos-primo.github.io/c2pro-dashboards/black/<slug>/

Reaproveita o gerador de vendas (gerar_vendas.py): mesmas metricas, mesmo funil, mesma consistencia.
Diferencas: o filtro e BLACK no nome da campanha (config "filtro"), o resultado principal e o LEAD,
e o dashboard junta varias contas de anuncio (config "contas"), com uma visao por conta e uma de todas.

config.json:
  {"nome": "...", "subtitulo": "...", "filtro": "BLACK", "criativos_gasto_minimo": 10,
   "contas": [{"id": "act_...", "nome": "Camila"}, {"id": "act_...", "nome": "Revenda"}]}

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
from gerar import api, criptografar, botao_atualizar

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
    }

    senha = os.environ.get(f"SENHA_BLACK_{slug.upper().replace('-', '_')}", "")
    json_dados = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    payload = criptografar(json_dados, senha) if senha else {"enc": False, "dados": dados}

    saida.mkdir(parents=True, exist_ok=True)
    html = (AQUI / "template_black.html").read_text(encoding="utf-8")
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    html = botao_atualizar(html, f"black/{slug}")
    (saida / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(AQUI / "logo.png", saida / "logo.png")

    partes = "; ".join(
        f"{n}: R$ {visoes[n]['periodos']['30']['atual']['gasto']:.2f}, {visoes[n]['periodos']['30']['atual']['leads']:.0f} leads, "
        f"{len(visoes[n]['periodos']['30']['campanhas'])} campanhas" for n in nomes)
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
