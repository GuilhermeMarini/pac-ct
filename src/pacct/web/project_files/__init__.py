"""Arquivos do Projeto: o acervo de RDB e SCD de um visitante.

    library.py   o acervo em si -- dedup por sha256, sem saber que ha HTTP
    handler.py   as rotas de /files/
    client.py    o runtime `SelLibrary`, injetado em toda pagina
    templates/   library.html

Antes disto cada ferramenta tinha o proprio painel de upload: o mesmo RDB de
40-140 MB era transferido uma vez por ferramenta, e dois uploads do mesmo SCD
eram dois arquivos. Aqui o acervo e' um so por sessao, e as ferramentas
escolhem dentro dele.
"""

from __future__ import annotations

from pacct.paths import PROJECT_FILES_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Read one template. Read at import time, like the GLV and the DNP map."""
    return (PROJECT_FILES_TEMPLATES_DIR / name).read_text(encoding="utf-8")
