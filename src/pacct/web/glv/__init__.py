"""Graphical Logic Viewer: N diagramas abertos, cada um com o seu rele.

    state.py      LiveState (+ clear)
    poll.py       as tres threads de polling, uma por familia de rele
    gle_pages.py  paginas, bits e analogicos de um GLE
    notes.py      notas, marca-texto e grupos, chaveados por nome de rele
    link.py       RelayLink + LinkPool: uma conexao por rele, contada por
                  referencia entre os diagramas que a pedem. So o ciclo de
                  vida: o protocolo mora no transporte
    transport/    o seam de transporte -- o Protocol em __init__.py e o
                  telnet (SEL Fast Message) em telnet.py
    diagram.py    GlvDiagram: um diagrama aberto, conectado ou nao
    handler.py    as rotas
    templates/    dashboard.html e landing.html

O que era `dashboard.py` inteiro. La ficaram so a home e o `main()`.
"""

from __future__ import annotations

from pacct.paths import GLV_TEMPLATES_DIR


def load_template(name: str) -> str:
    """Le um template do GLV. Lido no import, como a string raw era antes."""
    return (GLV_TEMPLATES_DIR / name).read_text(encoding="utf-8")
