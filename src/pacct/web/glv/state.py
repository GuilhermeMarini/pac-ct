"""Ultimo snapshot lido de um rele.

Mora aqui, e nao no diagrama, porque quem escreve nele sao as `poll_loop*` --
uma thread por RELE, nao por desenho. Dois diagramas abertos sobre o mesmo
rele leem o mesmo LiveState, que e' o certo: a Relay Word e' do rele.
"""

from __future__ import annotations

import threading
import time


class LiveState:
    """Mantem o ultimo snapshot de valores lidos do rele."""
    def __init__(self):
        self.lock = threading.Lock()
        self.digitals: dict[str, int] = {}
        self.analogs: dict[str, float] = {}
        # A leitura e' carimbada por DOIS relogios, e o segundo nao e' luxo.
        # O de parede (`last_update_ts`) e' a hora; o monotonico
        # (`last_update_mono`) e' o unico com que se pode medir IDADE. Medido
        # nesta maquina: o relogio do WSL estava 82,5 s atras do relogio do
        # Windows, e o navegador (que roda no Windows) fazia
        # `Date.now()/1000 - ts` -- 82,5 s de "valores antigos" com o fio
        # perfeito, e uma tela que parecia congelada porque a conta atravessa
        # dois relogios que nao concordam. Quem responde a idade agora e' o
        # servidor, com o relogio que nao anda pra tras.
        self.last_update_ts = 0.0
        self.last_update_mono = 0.0
        self.error = ""
        # Bits da pagina que o usuario esta vendo. So o modo `tar_digitals`
        # (3xx) usa isso: la cada linha da Relay Word custa ~200ms de round
        # trip, entao ler o diagrama inteiro a cada volta seria inviavel --
        # lemos so o que esta na tela. Preenchido pelo handler de /values.
        self.wanted_bits: set[str] = set()

    def set_wanted_bits(self, bits) -> None:
        with self.lock:
            self.wanted_bits = {b.upper() for b in bits}

    def mark_updated(self) -> None:
        """Carimba a leitura nos dois relogios. Chame COM `self.lock` na mao
        -- os quatro loops de polling ja escrevem os valores debaixo dele, e o
        carimbo tem que entrar na mesma seccao critica que eles."""
        self.last_update_ts = time.time()
        self.last_update_mono = time.monotonic()

    def snapshot(self):
        with self.lock:
            return {
                "digitals": dict(self.digitals),
                "analogs": {k: (str(v) if not isinstance(v, (int, float)) else v)
                            for k, v in self.analogs.items()},
                "ts": self.last_update_ts,
                # A idade em segundos, medida AQUI. `None` enquanto nada foi
                # lido -- que e' diferente de "zero segundos" e a tela trata
                # como tal.
                "age": (time.monotonic() - self.last_update_mono
                        if self.last_update_mono else None),
                "error": self.error,
            }

    def clear(self) -> None:
        """Volta tudo a indeterminado.

        Chamado ao desconectar: a tela nunca pode continuar mostrando um valor
        que nao esta sendo lido agora. `wanted_bits` fica -- e' o que a pagina
        aberta pediu, nao um valor lido.
        """
        with self.lock:
            self.digitals = {}
            self.analogs = {}
            self.last_update_ts = 0.0
            self.last_update_mono = 0.0
            self.error = ""
