"""Origem dos leads (pago x organico) a partir da planilha de captura de UTM do cliente.

Usado pelo gerar_black.py quando o config tem "origem_leads":
  {"planilha": "<id da planilha>", "gid": "0", "resumo": "origem.json"}

Cada linha da planilha e um cadastro na pagina, com as UTMs que vieram na URL. A regra:
  - PAGO: UTM de anuncio da Meta (utm_source ig/fb/an/msg, utm_medium "paid", campanha C2PRO ou macro {{...}}
    que a Meta nao substituiu).
  - ORGANICO: UTM de canal proprio, separado por canal: bio do Instagram, WhatsApp e e-mail.
  - NAO TRAQUEADO: cadastro sem UTM nenhuma, nao da pra saber de onde veio.
  - Fora da conta: so os cadastros de teste.
  - Todo cadastro conta, como a Meta conta cada envio do formulario (inclusive os que chegaram com e-mail "null",
    quando a captura falhou mas a UTM veio). A parte ("u"), quantos sao pessoas novas pelo e-mail ou telefone.

O repositorio e publico, entao nenhum e-mail, nome ou telefone sai daqui: so contagens por dia, origem, canal,
campanha e anuncio. Se a planilha nao estiver aberta para "qualquer pessoa com o link", o gerador usa o resumo
salvo em black/<slug>/<resumo> (mesmo formato, sem dado pessoal). Para regravar esse resumo a partir de um CSV
baixado da planilha:
    python origem_leads.py <slug> caminho/do/arquivo.csv
"""

import collections
import csv
import datetime as dt
import io
import json
import re
import sys
from pathlib import Path

import requests

AQUI = Path(__file__).resolve().parent

FONTES_PAGAS = {"ig", "fb", "an", "msg", "facebook", "meta", "audience_network", "messenger"}
TESTES_EMAIL = ("traqueamento", "thxmarketing")


def _limpo(v):
    v = (v or "").strip()
    return "" if v.lower() in ("undefined", "null", "none", "(not set)") else v


def classificar(linha):
    """-> (origem, canal) ou None para teste. origem: 'pago', 'organico' ou 'nao_traqueado' (sem UTM nenhuma)."""
    src, med, camp = (_limpo(linha.get(k)).lower() for k in ("utm_source", "utm_medium", "utm_campaign"))
    email = _limpo(linha.get("Email")).lower()
    if src == "teste" or "teste" in camp or any(t in email for t in TESTES_EMAIL):
        return None
    if not src and not med and not camp:
        return "nao_traqueado", "Não traqueado (sem UTM)"
    if src in FONTES_PAGAS or med == "paid" or camp.startswith("c2pro") or "{{" in src + camp:
        return "pago", "Meta Ads"
    if src in ("instagram", "insta") or med == "bio":
        return "organico", "Instagram (bio e perfil)"
    if "whats" in src + med or src == "crm":
        return "organico", "WhatsApp"
    if "email" in med or "e-mail" in med or "rd station" in src:
        return "organico", "E-mail"
    if not src:  # tem algum pedaco de UTM, mas sem a fonte nao da pra dizer o canal
        return "nao_traqueado", "Não traqueado (sem UTM)"
    return "organico", f"Outros ({src})"


def _anuncio(v):
    # A Meta as vezes cola o id do anuncio no fim do nome (AD01_..._VIDEO120249652827120141).
    v = _limpo(v)
    if "{{" in v:  # macro que a Meta nao substituiu
        return "(UTM do anúncio não preenchida)"
    return re.sub(r"(?<=[A-Za-z])\d{12,}$", "", v) or "(sem nome)"


def resumir(linhas):
    """Linhas cruas da planilha -> resumo sem dado pessoal: contagens por dia/origem/canal/campanha/anuncio."""
    vistos, cont, unicos = set(), collections.Counter(), collections.Counter()
    fora = collections.Counter()
    for l in linhas:
        c = classificar(l)
        if c is None:
            fora["teste"] += 1
            continue
        try:
            data = dt.datetime.strptime(l["Data"].strip()[:10], "%d/%m/%Y").date().isoformat()
        except ValueError:
            fora["sem_data"] += 1
            continue
        origem, canal = c
        camp = _limpo(l.get("utm_campaign")) if origem == "pago" else ""
        camp = "(UTM da campanha não preenchida)" if "{{" in camp else camp
        anuncio = _anuncio(l.get("utm_content")) if origem == "pago" else ""
        chave_reg = (data, origem, canal, camp, anuncio)
        # Todo cadastro conta (como a Meta conta cada envio do formulario). A parte, quantos sao pessoas novas.
        cont[chave_reg] += 1
        email = _limpo(l.get("Email")).lower()
        fone = re.sub(r"\D", "", l.get("fone") or "")[-11:]
        chave = email or fone
        if not chave:
            fora["sem_contato"] += 1  # a captura falhou (e-mail "null"), mas o cadastro veio com UTM e conta
            unicos[chave_reg] += 1
        elif chave in vistos:
            fora["repetido"] += 1
        else:
            vistos.add(chave)
            unicos[chave_reg] += 1
    registros = [{"data": d, "origem": o, "canal": c, "campanha": cp, "anuncio": a, "n": n,
                  "u": unicos[(d, o, c, cp, a)]} for (d, o, c, cp, a), n in sorted(cont.items())]
    return {"ate": max((r["data"] for r in registros), default=""), "linhas": len(linhas),
            "fora": dict(fora), "registros": registros}


def ler_planilha(cfg):
    """Linhas da planilha publica (CSV) ou None se ela nao estiver aberta para quem tem o link."""
    url = f"https://docs.google.com/spreadsheets/d/{cfg['planilha']}/export?format=csv&gid={cfg.get('gid', '0')}"
    try:
        resp = requests.get(url, timeout=60)
    except requests.RequestException:
        return None
    if resp.status_code != 200 or "text/csv" not in resp.headers.get("content-type", ""):
        return None
    return list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))


def carregar(slug, cfg):
    """-> (resumo, fonte). fonte: 'planilha' (lida agora) ou 'resumo salvo'."""
    linhas = ler_planilha(cfg)
    if linhas is not None:
        return resumir(linhas), "planilha"
    arq = AQUI / "black" / slug / cfg.get("resumo", "origem.json")
    if not arq.exists():
        return None, ""
    return json.loads(arq.read_text(encoding="utf-8")), "resumo salvo"


def _agrupar(regs, chave):
    c = collections.Counter()
    for r in regs:
        c[r[chave]] += r["n"]
    return [{"nome": k, "leads": v} for k, v in sorted(c.items(), key=lambda kv: -kv[1])]


def _bloco(regs):
    soma = lambda o: sum(r["n"] for r in regs if r["origem"] == o)
    pago, org, nt = soma("pago"), soma("organico"), soma("nao_traqueado")
    return {"total": pago + org + nt, "pago": pago, "organico": org, "nao_traqueado": nt,
            "unicos": sum(r.get("u", r["n"]) for r in regs)}


def periodos(resumo, periodos_meta, ontem, ini30):
    """Mesmos periodos do dashboard da Meta (datas iguais), mais a serie diaria de 30 dias."""
    regs = resumo["registros"]
    entre = lambda a, b: [r for r in regs if a <= r["data"] <= b]
    saida = {}
    for k, p in periodos_meta.items():
        atual = entre(p["inicio"], p["fim"])
        pagos = [r for r in atual if r["origem"] == "pago"]
        saida[k] = {"atual": _bloco(atual), "anterior": _bloco(entre(p["ant_inicio"], p["ant_fim"])),
                    "canais": _agrupar(atual, "canal"),
                    "campanhas": _agrupar(pagos, "campanha"), "anuncios": _agrupar(pagos, "anuncio")}
    datas = [(ini30 + dt.timedelta(days=n)).isoformat() for n in range((ontem - ini30).days + 1)]
    diario = [{"data": d, **_bloco([r for r in regs if r["data"] == d])} for d in datas]
    return saida, diario


if __name__ == "__main__":
    slug, caminho = sys.argv[1], sys.argv[2]
    cfg = json.loads((AQUI / "black" / slug / "config.json").read_text(encoding="utf-8"))["origem_leads"]
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        r = resumir(list(csv.DictReader(f)))
    destino = AQUI / "black" / slug / cfg.get("resumo", "origem.json")
    destino.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    tot = collections.Counter()
    for x in r["registros"]:
        tot[x["origem"]] += x["n"]
    print(f"OK {destino}: ate {r['ate']}, {dict(tot)}, fora da conta {r['fora']}")
