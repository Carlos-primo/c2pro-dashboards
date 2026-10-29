"""Dashboard com duas abas no mesmo link: CONTATOS e DISTRIBUICAO (Meta Ads).

Ativado por cliente com "modelo": "contatos_distribuicao" no clientes/<slug>/config.json. O gerar.py chama gerar()
aqui no lugar do dashboard padrao, entao o link e o agendamento (2h) continuam os de distribuicao.

config.json:
  {"nome": "...", "subtitulo": "...", "conta_anuncio": "act_...", "criativos_gasto_minimo": 10,
   "modelo": "contatos_distribuicao"}

Cada aba e o dashboard aprovado do seu tipo, com as metricas do objetivo dela:
  Contatos:     template_leads.html (metrica principal = contatos), so campanhas com CONTATOS no nome.
                Mesma regra do dashboard de contatos: sem investimento, custo ou comparacao.
  Distribuicao: template.html, so campanhas com DISTRIBUICAO no nome (FILTRO fixo do gerar.py).
As duas paginas vao inteiras dentro do index.html (iframe srcdoc), entao o site continua com um arquivo so por cliente.
"""

import html as htmllib
import json
import shutil

import gerar as base
import gerar_leads as leads

AQUI = base.AQUI

# Regra da agencia, igual ao FILTRO de distribuicao: fixo no codigo, nao configuravel por cliente.
FILTRO_CONTATOS = "CONTATOS"

# Dentro do iframe a pagina nao pode ter altura minima de tela (a altura do iframe acompanha o conteudo)
# e o fundo fica com a pagina de fora, pra nao repetir o degrade.
CSS_INTERNO = "<style>html,body{background:transparent!important;min-height:0!important}</style></head>"


def pagina(template, dados):
    html = (AQUI / template).read_text(encoding="utf-8")
    payload = {"enc": False, "dados": dados}
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    return html.replace("</head>", CSS_INTERNO, 1)


def embutir(html):
    """String JS segura dentro de <script>."""
    return json.dumps(html, ensure_ascii=False).replace("</", "<\\/")


def gerar(slug):
    # Le o config de novo: rodando "python gerar.py", o CFG do __main__ nao e o deste modulo.
    cfg = json.loads((AQUI / "clientes" / slug / "config.json").read_text(encoding="utf-8"))

    base.CFG.clear()
    base.CFG.update({**cfg, "subtitulo": "Campanhas de distribuição"})
    dist = base.montar_dados()

    leads.CFG.clear()
    leads.CFG.update({"nome": cfg["nome"], "subtitulo": "Campanhas de contatos", "conta_anuncio": cfg["conta_anuncio"],
                      "metrica_principal": "contatos", "filtro_campanha": FILTRO_CONTATOS})
    cont = leads.montar_dados(slug)

    html = (AQUI / "template_contatos_distribuicao.html").read_text(encoding="utf-8")
    html = (html.replace("__NOME__", htmllib.escape(cfg["nome"]))
                .replace("__CONTATOS__", embutir(pagina("template_leads.html", cont)))
                .replace("__DISTRIBUICAO__", embutir(pagina("template.html", dist))))

    saida = base.SITE / slug
    saida.mkdir(parents=True, exist_ok=True)
    (saida / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(AQUI / "logo.png", saida / "logo.png")

    c30, d30 = cont["periodos"]["30"], dist["periodos"].get("30") or dist["periodos"]["fixo"]
    print(f"OK {slug} (contatos + distribuicao): 30d {c30['atual']['contatos']:.0f} contatos em "
          f"{len(c30['campanhas'])} campanhas {FILTRO_CONTATOS} | distribuicao {d30['inicio']} a {d30['fim']} R$ {d30['atual']['gasto']:.2f}, "
          f"{len(d30['campanhas'])} campanhas, {len(d30['anuncios'])} anuncios | link aberto, sem senha")
