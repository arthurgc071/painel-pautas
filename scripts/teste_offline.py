"""Teste sem internet: simula os feeds e a resposta do Claude.
Rode com:  python scripts/teste_offline.py
Gera docs/data/news.json com dados FICTÍCIOS, só para ver o painel funcionando."""

import json
import sys
import types
from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import collect  # noqa: E402

N = collect.NOW
FAKE = {
    "ECONOMIA": [
        ("Copom mantém Selic e sinaliza cautela com inflação de serviços", 3),
        ("Banco Central mantém juros; comunicado cita inflação de serviços", 4),
        ("Dólar recua após decisão do Copom e fala de diretor do BC", 6),
        ("IPCA de outubro vem abaixo do esperado puxado por energia", 20),
        ("Inflação oficial desacelera em outubro, diz IBGE", 21),
        ("Governo apresenta proposta de revisão de gastos obrigatórios", 30),
        ("Matéria velha que deve ser descartada", 70),
    ],
    "STARTUPS": [
        ("Fintech brasileira levanta rodada série B para expandir crédito a PMEs", 5),
        ("Startup de crédito para PMEs capta série B com fundo americano", 7),
        ("Healthtech de telemedicina compra concorrente e mira interior", 15),
        ("Empresa de IA lança modelo em português para atendimento", 26),
        ("Varejista abre programa de inovação aberta com startups", 40),
    ],
}


def rss(items):
    body = "".join(
        f"<item><title>{t} - Veículo Exemplo</title><link>https://exemplo.com/{abs(hash(t))}</link>"
        f"<pubDate>{format_datetime(N - timedelta(hours=h))}</pubDate>"
        f"<source url='https://exemplo.com'>Veículo Exemplo</source></item>"
        for t, h in items
    )
    return f"<?xml version='1.0'?><rss><channel>{body}</channel></rss>"


def fake_fetch(url):
    import feedparser
    for frente, items in FAKE.items():
        key = "Selic" if frente == "ECONOMIA" else "startup"
        if key in url or ("infomoney" in url and frente == "ECONOMIA") or ("techcrunch" in url and frente == "STARTUPS"):
            return feedparser.parse(rss(items))
    # buscas de contexto
    return feedparser.parse(rss([("Entenda: como funciona o tema em 5 pontos", 100),
                                 ("Análise: o que especialistas esperam para 2027", 150)]))


class FakeClient:
    chamadas = []

    def __init__(self, **k):
        self.messages = self

    def create(self, **k):
        nome = k["tool_choice"]["name"]
        p = k["messages"][0]["content"]
        econ = "economia, mercado" in p
        FakeClient.chamadas.append(nome)
        assert k["max_tokens"] <= 4000
        if nome == "selecionar_historias":
            if econ:
                h = [{"codigos": ["e0", "e1", "e2"], "titulo": "Copom mantém a Selic e o dólar reage", "nota": 9},
                     {"codigos": ["e3", "e4"], "titulo": "Inflação de outubro desacelera", "nota": 7}]
            else:
                h = [{"codigos": ["e0", "e1"], "titulo": "Fintech de crédito para PMEs capta série B",
                      "nota": 8, "empresa": "Fintech Exemplo"}]
            out = {"historias": h}
        else:
            if "Inflação de outubro" in p:  # simula uma pauta que falha
                return types.SimpleNamespace(stop_reason="max_tokens", content=[])
            out = {
                "resumo": "Resumo fictício de teste em duas frases. Serve só para ver o layout.",
                "por_que_importa": "Impacto fictício para teste.",
                "angulos": ["Ângulo um", "Ângulo dois", "Ângulo três"],
                "ganchos": ["Gancho de abertura de exemplo?", "Outro gancho"],
                "perguntas": ["Dúvida comum?"],
                "busca_contexto": "selic como funciona" if econ else "Fintech Exemplo",
                "busca_social": "selic hoje" if econ else "fintech pme",
                "hashtags": ["selic", "economia"] if econ else ["startups", "fintech"],
            }
            if not econ:
                assert "raio_x" in k["tools"][0]["input_schema"]["properties"]
                out.update({
                    "empresa": "Fintech Exemplo",
                    "raio_x": {
                        "o_que_faz": "Empresta dinheiro para pequenas empresas que os bancos costumam recusar.",
                        "como_funciona": "Analisa as vendas da empresa em tempo real para decidir o crédito.",
                        "como_ganha_dinheiro": "Juros sobre os empréstimos.",
                        "o_momento": "Captou uma rodada série B (valor a confirmar).",
                        "a_checar": ["Valor exato da rodada", "Quem liderou o investimento"],
                    },
                    "estrutura_video": ["Gancho: pergunta provocativa", "O problema: crédito caro para PME",
                                        "Como a empresa resolve", "O que aconteceu agora",
                                        "Fechamento: o que vem depois"],
                })
        return types.SimpleNamespace(stop_reason="tool_use", content=[
            types.SimpleNamespace(type="tool_use", name=nome, input=out)])


collect.fetch_feed = fake_fetch
sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=FakeClient)

if __name__ == "__main__":
    import os
    modo = sys.argv[1] if len(sys.argv) > 1 else "claude"
    if modo == "claude":
        os.environ["ANTHROPIC_API_KEY"] = "teste"
    else:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    collect.main()
    d = json.loads((collect.OUT_DIR / "news.json").read_text())
    for f, ss in d["frentes"].items():
        print(f, len(ss), [s["titulo"] for s in ss])
        assert all(s["materias"] for s in ss)
    assert "velha" not in json.dumps(d), "filtro de 50h falhou"
    if modo == "claude":
        print("chamadas ao Claude:", FakeClient.chamadas)
        econ = {s["titulo"]: s for s in d["frentes"]["ECONOMIA"]}
        assert econ["Copom mantém a Selic e o dólar reage"]["angulos"], "pauta boa sumiu"
        assert not econ["Inflação de outubro desacelera"]["angulos"], "falha não foi isolada"
        assert d["frentes"]["STARTUPS"][0]["raio_x"]["a_checar"]
    print("OK")
