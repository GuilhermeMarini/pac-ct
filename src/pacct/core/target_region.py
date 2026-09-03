"""
Acesso a regiao TARGET do banco Fast Message dos reles SEL-400.

Os reles SEL-411L (e outros da serie 400) NAO retornam nomes uteis no bloco
DNA do Fast Meter -- o bloco vem todo com placeholders '*'. Os nomes reais
da Relay Word (Z1T, 50P1, TRIP, etc.) vivem em outro banco de dados
("Communications Database") acessivel via dois caminhos:

  1. ASCII via comandos `MAP 1:TARGET` / `VIEW 1:TARGET` / `TAR <bit>`
  2. Binario via SEL Fast Message protocol (cabecalho A546h) com Function
     Codes 30h/31h/33h/10h

Este modulo implementa o caminho ASCII completamente (funciona end-to-end)
e fornece um stub do caminho binario explicando por que nao e implementavel
sem documentacao adicional da SEL.

Referencias:
  - SEL-411L Instruction Manual, pages 626-634 (Communications Database)
  - SEL-400 Series Manual, pages 1398-1400 (SEL Protocol section)
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pacct.core.relay_conn import channel_for

CACHE_VERSION = 1

# Regex para extrair o array de bytes da resposta de VIEW 1:TARGET
# Formato:  "TARGET = C0h,00h,02h,...,FFh"
_RE_TARGET_BYTES = re.compile(rb"TARGET\s*=\s*([0-9A-Fa-fh,\s]+)", re.IGNORECASE)
_RE_HEX_BYTE = re.compile(rb"([0-9A-Fa-f]{1,2})h")

# Regex para o header de TAR <bit>: linha com 8 nomes separados por espacos
# Ex: "Z1T     Z2T     Z3T     Z4T     Z5T     *       *       *"
_RE_TAR_HEADER = re.compile(rb"^([A-Za-z0-9_*]+(?:\s+[A-Za-z0-9_*]+){7})\s*$", re.MULTILINE)

# Quantos bytes a regiao TARGET do banco Fast Message carrega. Medido nos
# reles de bancada 4xx: 411L-A-R133 expoe 506 linhas de Relay Word e o
# `VIEW 1:TARGET` devolve o array inteiro; 500 e' o piso que o poll exige
# antes de aceitar a leitura como completa.
TARGET_REGION_BYTES = 500


def target_bytes_from_stream(buf: bytes, count: int = TARGET_REGION_BYTES):
    """Extrai os bytes da regiao TARGET de um stream que pode estar incompleto.

    Devolve `bytes` com exatamente `count` posicoes, ou `None` enquanto o
    stream ainda nao tem todos eles -- e' assim que o poll sabe que precisa
    continuar lendo.

    Existe como funcao publica porque o formato de `VIEW 1:TARGET` e' desta
    modulo. Antes o `poll_loop` importava `_RE_TARGET_BYTES` e `_RE_HEX_BYTE`
    daqui -- dois nomes privados de outro modulo -- e fazia isso A CADA VOLTA
    do loop de espera, dentro do caminho quente.
    """
    match = _RE_TARGET_BYTES.search(buf)
    if not match:
        return None
    hexes = _RE_HEX_BYTE.findall(match.group(1))
    if len(hexes) < count:
        return None
    return bytes(int(h, 16) for h in hexes[:count])

# Regex para linhas com endereco hex no inicio. Captura linhas como:
#   "3004h   <8 nomes>"  ou
#   "3004h   TARGET   char[488]   N1 N2 N3 N4 N5 N6 N7 N8"
# A primeira capturanca pega o endereco (3 ou 4 digitos hex + 'h'), a segunda
# pega o restante da linha (que sera parseado em busca dos 8 nomes).
_RE_ADDR_LINE = re.compile(
    rb"^\s*([0-9A-Fa-f]{3,4})h?\s+(.+?)\s*$",
    re.MULTILINE,
)
# TARGET no SEL-411L comeca em 3004h. Aceita 3000h..3FFFh por seguranca.
_TARGET_ADDR_START = 0x3004
_TARGET_ADDR_END = 0x3FFF


@dataclass
class TargetLayout:
    """Mapeamento bit_name -> (row_index, bit_position) na regiao TARGET."""
    bit_to_pos: dict = field(default_factory=dict)
    row_to_names: dict = field(default_factory=dict)  # row_idx -> [8 nomes]
    not_findable: set = field(default_factory=set)    # bits que tentamos e nao acha


# =============================================================================
# Caminho ASCII (FUNCIONA)
# =============================================================================

class AsciiTargetReader:
    """
    Le bits da Relay Word atraves dos comandos ASCII MAP/VIEW/TAR.

    Uso tipico:
        reader = AsciiTargetReader(client)
        reader.discover_bits(['Z1T', '50P1', 'TRIP'])  # uma vez
        valores = reader.read(['Z1T', '50P1', 'TRIP'])  # a cada poll
    """

    def __init__(self, client, logger=None):
        self.client = client
        self.logger = logger
        self._channel = None
        self.layout = TargetLayout()
        # Threshold: se `len(bits) <= tar_threshold`, usa TAR <bit> em vez de
        # VIEW 1:TARGET. TAR e mais leve (~80ms) que VIEW (~220ms) para poucos
        # bits, mas para muitos bits VIEW e melhor (1 round-trip).
        self.tar_threshold = 2

    @property
    def channel(self):
        """O MESMO canal que o loop de poll usa (`channel_for` memoiza por
        cliente). E' por ele que os dois combinam se o buffer esta limpo:
        antes esse aperto de mao era um `_buffer_clean` pendurado no objeto do
        selprotopy, escrito pelos dois lados.

        Preguicoso de proposito: metade desta classe (carregar/salvar cache,
        montar layout) nao fala com rele nenhum, e os testes desse pedaco
        constroem o reader com `client=None`.
        """
        if self._channel is None:
            self._channel = channel_for(self.client)
        return self._channel

    # --- cache persistente em arquivo JSON -----------------------------------

    @staticmethod
    def cache_path_for(relay_fid: str, cache_dir: Path | str | None = None) -> Path:
        """
        Calcula o caminho do arquivo de cache para o FID do rele.
        Sanitiza o FID para um nome de arquivo seguro.
        """
        if cache_dir is None:
            from pacct.paths import CACHE_DIR
            cache_dir = CACHE_DIR
        else:
            cache_dir = Path(cache_dir)
        sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", relay_fid or "unknown")
        return cache_dir / f"{sanitized}.json"

    def load_cache(self, path: Path | str, fid: str = "") -> bool:
        """
        Tenta carregar o layout (bit_to_pos + row_to_names) do cache.
        Retorna True se carregou com sucesso, False se nao existe / esta
        invalido / FID diferente.
        """
        path = Path(path)
        if not path.is_file():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            if self.logger:
                self.logger.warning(f"  cache invalido em {path}: {e}")
            return False

        if payload.get("version") != CACHE_VERSION:
            if self.logger:
                self.logger.info("  cache versao incompativel; ignorando")
            return False
        if fid and payload.get("fid") and payload["fid"] != fid:
            if self.logger:
                self.logger.info(
                    f"  cache pertence a outro firmware (cache={payload['fid']!r}, "
                    f"rele={fid!r}); ignorando"
                )
            return False

        bit_to_pos = payload.get("bit_to_pos", {})
        row_to_names = payload.get("row_to_names", {})
        not_findable = payload.get("not_findable", [])
        # JSON nao tem tupla nem int-key; reconvertendo:
        self.layout.bit_to_pos = {
            k: (int(v[0]), int(v[1])) for k, v in bit_to_pos.items()
        }
        self.layout.row_to_names = {
            int(k): list(v) for k, v in row_to_names.items()
        }
        self.layout.not_findable = set(s.upper() for s in not_findable)
        if self.logger:
            self.logger.info(
                f"  cache carregado de {path.name}: "
                f"{len(self.layout.bit_to_pos)} bits, "
                f"{len(self.layout.row_to_names)} linhas, "
                f"descoberto em {payload.get('discovered_at', '?')}"
            )
        return True

    def save_cache(self, path: Path | str, fid: str = "",
                   devid: str = "") -> None:
        """Salva o layout atual em JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "fid": fid,
            "devid": devid,
            "discovered_at": datetime.now().isoformat(timespec="seconds"),
            "bit_to_pos": {k: list(v) for k, v in self.layout.bit_to_pos.items()},
            "row_to_names": {str(k): v for k, v in self.layout.row_to_names.items()},
            "not_findable": sorted(self.layout.not_findable),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if self.logger:
            self.logger.info(
                f"  cache salvo em {path.name} "
                f"({len(self.layout.bit_to_pos)} bits)"
            )

    # --- comunicacao basica --------------------------------------------------

    def _send_ascii(self, cmd: bytes, wait: float = 0.0,
                    quiet_period: float = 0.08,
                    deadline_s: float = 2.5) -> bytes:
        """
        Envia comando ASCII e retorna a resposta crua.

        Otimizado para baixa latencia:
          - drain so se buffer nao esta conhecidamente limpo
          - sem `time.sleep(wait)` apos o write
          - espera no socket, nao a 10ms fixos
          - marca o canal como limpo ao terminar
        """
        # Drain rapido sempre (custa <15ms, evita contaminacao entre TAR's)
        try:
            for _ in range(3):
                if not self.channel.read_available():
                    break
                time.sleep(0.005)
        except Exception:
            pass

        self.channel.mark_dirty()
        self.channel.send_ascii(cmd)
        if wait > 0:
            time.sleep(wait)

        chunks = []
        deadline = time.monotonic() + deadline_s
        last_data = time.monotonic()
        while time.monotonic() < deadline:
            try:
                chunk = self.channel.read_available()
            except Exception:
                chunk = b""
            if chunk:
                chunks.append(chunk)
                last_data = time.monotonic()
            elif time.monotonic() - last_data > quiet_period:
                break
            else:
                # Espera no socket em vez de acordar a 100 Hz. E' daqui que
                # vinha quase toda a queima de CPU do modo 3xx: um `TAR
                # <linha>` custa ~200 ms NO RELE, e o poll faz um por linha
                # ocupada da pagina -- medido num 311C-1-R509, 1913 leituras
                # vazias de socket em 20 s de polling.
                self.channel.wait_readable(
                    min(0.05, max(0.0, deadline - time.monotonic())))
        # Buffer drenou ate quiet_period -> esta limpo no prompt
        self.channel.mark_clean()
        return b"".join(chunks)

    # --- discovery: descobre row/bit-position de cada bit pedido -------------

    def discover_bits(self, bit_names: list[str]) -> dict[str, tuple[int, int]]:
        """
        Para cada bit pedido, envia `TAR <bit>` e descobre em qual linha e
        posicao ele esta. Atualiza self.layout.bit_to_pos.

        Bits que falham (TAR retorna nada, ou row index nao localizavel) sao
        marcados em layout.not_findable e pulados em proximas chamadas.

        Retorna mapa: {bit_name: (row, bit_position_msb)}.
        """
        for bit in bit_names:
            bit_upper = bit.upper()
            if bit_upper in self.layout.bit_to_pos:
                continue  # ja conhecido
            if bit_upper in self.layout.not_findable:
                continue  # ja sabemos que nao acha

            resp = self._send_ascii(f"TAR {bit_upper}".encode(), wait=1.0)
            row_info = self._parse_tar_header(resp)
            if row_info is None:
                self.layout.not_findable.add(bit_upper)
                if self.logger:
                    self.logger.warning(f"  bit '{bit}' nao encontrado via TAR")
                continue
            names_row = row_info
            try:
                _ = names_row.index(bit_upper)
            except ValueError:
                self.layout.not_findable.add(bit_upper)
                continue

            row_idx = self._find_row_index_for(names_row)
            if row_idx is None:
                self.layout.not_findable.add(bit_upper)
                if self.logger:
                    self.logger.warning(
                        f"  bit '{bit}' encontrado mas sem row index"
                    )
                continue

            self.layout.row_to_names[row_idx] = names_row
            for i, name in enumerate(names_row):
                if name == "*":
                    continue
                msb_pos = 7 - i
                self.layout.bit_to_pos[name] = (row_idx, msb_pos)

        return self.layout.bit_to_pos

    def discover_via_map_bl(self, debug_dump: Path | str | None = None) -> int:
        """
        Descobre todos os bits da Relay Word numa unica chamada `MAP 1 TARGET BL`.

        Substitui o varredor `TAR 0..499` (~40s) por 1 round-trip (~1-3s).
        O comando lista cada byte da regiao TARGET (3004h..) com seus 8 bit
        labels em ordem MSB->LSB (manual SEL-400 series, pag. 14.46).

        Parametros
        ----------
        debug_dump : Path | str | None
            Se fornecido, salva a resposta crua nesse arquivo (util para
            ajustar o parser se o formato do firmware diferir).

        Retorna
        -------
        int : numero de bits descobertos. 0 indica falha de parse (chamador
              pode cair no fallback `discover_all_rows`).
        """
        # Resposta pode chegar a ~30-50KB (488 bytes * 8 nomes * ~6 chars).
        # Usa quiet_period maior pq o rele as vezes pausa entre paginas.
        resp = self._send_ascii(
            b"MAP 1 TARGET BL",
            wait=0.0,
            quiet_period=0.3,
            deadline_s=15.0,
        )
        if debug_dump:
            try:
                Path(debug_dump).parent.mkdir(parents=True, exist_ok=True)
                Path(debug_dump).write_bytes(resp)
                if self.logger:
                    self.logger.info(
                        f"  resposta crua de MAP 1 TARGET BL salva em {debug_dump} "
                        f"({len(resp)} bytes)"
                    )
            except OSError:
                pass

        added = self._parse_map_bl(resp)
        if self.logger:
            if added:
                self.logger.info(
                    f"  MAP 1 TARGET BL: {added} bits em "
                    f"{len(self.layout.row_to_names)} linhas"
                )
            else:
                self.logger.warning(
                    "  MAP 1 TARGET BL: nenhum bit reconhecido no response. "
                    "Verifique o dump bruto para ajustar o parser."
                )
        return added

    def _parse_map_bl(self, resp: bytes) -> int:
        """
        Parseia a resposta de `MAP 1 TARGET BL`.

        Formato esperado (uma linha por byte do TARGET):
            <addr>h   <8 nomes ou '*'>
        ou:
            <addr>h   TARGET   char[N]   <8 nomes ou '*'>

        Para cada linha valida em 3004h..31F7h, calcula `row_idx = addr - 3004h`
        e popula `layout.row_to_names[row_idx]` + `layout.bit_to_pos`.

        Retorna numero de bits NOVOS adicionados a `bit_to_pos`.
        """
        before = len(self.layout.bit_to_pos)
        # Token = identificador valido da Relay Word ou '*' (slot vazio).
        # Aceita nomes como Z1T, 50P1, TRIPLED, AMV001, etc.
        #
        # O primeiro caractere PODE ser digito, e isso nao e' um detalhe: um
        # elemento de protecao se chama pelo numero ANSI dele -- 50P1 e 50P2
        # (sobrecorrente instantanea), 51T (temporizada), 27P1 (subtensao),
        # 59, 81, 3PO. Sao a maior parte da Relay Word de um rele de protecao.
        #
        # A regex antiga exigia `[A-Za-z_]` no inicio e errava de dois jeitos,
        # os dois silenciosos. Se um nome com digito caia no meio da linha, a
        # varredura invertida parava e a linha INTEIRA sumia -- com os outros
        # sete nomes juntos. E se caia no fim (o LSB), ele era pulado e a
        # varredura seguia pra esquerda ate juntar oito, engolindo a palavra
        # `TARGET` do prefixo de ruido como se fosse bit: a linha virava
        # ['TARGET', 'TRIP', 'Z1T', ...] e TODOS os bits reais andavam uma
        # posicao. `TRIP` passava a ser lido do bit 6 em vez do 7, e a GLV
        # pintava no diagrama o estado de outro bit sem nada parecer errado.
        tok_re = re.compile(rb"^[A-Za-z0-9_]+$")

        for m in _RE_ADDR_LINE.finditer(resp):
            try:
                addr = int(m.group(1), 16)
            except ValueError:
                continue
            if not (_TARGET_ADDR_START <= addr <= _TARGET_ADDR_END):
                continue
            tail = m.group(2)
            tokens = tail.split()
            # Pega os ULTIMOS 8 tokens que sao identificadores ou '*'.
            # Se a linha tem ruido tipo "TARGET char[488]" antes, ignora.
            name_tokens = []
            for t in reversed(tokens):
                if t == b"*" or tok_re.match(t):
                    name_tokens.append(t)
                    if len(name_tokens) == 8:
                        break
                else:
                    # token nao-nome encontrado: se ja temos 8, ok; senao reset
                    if name_tokens:
                        break
            if len(name_tokens) != 8:
                continue
            name_tokens.reverse()  # voltamos para ordem MSB->LSB
            names = [t.decode("utf-8", errors="replace").upper() for t in name_tokens]

            row_idx = addr - _TARGET_ADDR_START
            self.layout.row_to_names[row_idx] = names
            for i, nm in enumerate(names):
                if nm == "*":
                    continue
                if nm not in self.layout.bit_to_pos:
                    self.layout.bit_to_pos[nm] = (row_idx, 7 - i)

        return len(self.layout.bit_to_pos) - before

    def discover_all_rows(self, max_rows: int = 500) -> dict[int, list[str]]:
        """
        Varre `TAR 0`..`TAR max_rows-1` e popula o mapa completo de nomes.
        Usar quando o usuario quer "*" ou "*ON" (mostrar todos).
        """
        for row in range(max_rows):
            if row in self.layout.row_to_names:
                continue
            resp = self._send_ascii(f"TAR {row}".encode(), wait=0.6)
            names = self._parse_tar_header(resp)
            if names is None:
                continue
            self.layout.row_to_names[row] = names
            for i, name in enumerate(names):
                if name == "*":
                    continue
                msb_pos = 7 - i
                if name not in self.layout.bit_to_pos:
                    self.layout.bit_to_pos[name] = (row, msb_pos)
            # Heuristica de early-termination: pula apenas se vermos uma
            # sequencia longa de linhas all-'*'. O TARGET do SEL-411L tem
            # varios trechos com 3+ linhas all-'*' no meio (77-79, 249-251,
            # 453-455, 489-491), entao exigimos pelo menos 30 linhas vazias
            # consecutivas para considerar que chegamos ao fim da regiao.
            if all(n == "*" for n in names) and row > 30:
                empty_count = 1
                for next_row in range(row + 1, row + 30):
                    resp = self._send_ascii(f"TAR {next_row}".encode(), wait=0.4)
                    next_names = self._parse_tar_header(resp)
                    if next_names is None:
                        # TAR retornou erro/vazio -- provavelmente fora do range
                        empty_count += 1
                    elif all(n == "*" for n in next_names):
                        empty_count += 1
                        self.layout.row_to_names[next_row] = next_names
                    else:
                        # Achou nomes -> nao e o fim, retomar varredura normal
                        self.layout.row_to_names[next_row] = next_names
                        for i, name in enumerate(next_names):
                            if name == "*":
                                continue
                            if name not in self.layout.bit_to_pos:
                                self.layout.bit_to_pos[name] = (next_row, 7 - i)
                        empty_count = 0
                        break
                if empty_count >= 30:
                    break
        return self.layout.row_to_names

    def _parse_tar_header(self, resp: bytes) -> list[str] | None:
        """Extrai os 8 nomes da resposta de um TAR <x>."""
        # Tira linhas de comando/eco
        for line in resp.split(b"\r"):
            line = line.strip()
            if not line:
                continue
            # Pula linhas com numeros (que sao os valores)
            tokens = line.split()
            if len(tokens) != 8:
                continue
            # Heuristica: header tem letras; valores sao apenas 0 e 1
            if all(tok in (b"0", b"1") for tok in tokens):
                continue
            return [tok.decode("utf-8", errors="replace").upper() for tok in tokens]
        return None

    def _find_row_index_for(self, names: list[str]) -> int | None:
        """
        Dado um conjunto de 8 nomes (header de uma linha), descobre o numero
        absoluto da linha procurando incrementalmente via `TAR <row>`.
        Cacheia o resultado.
        """
        # Otimizacao: muitos bits aparecem nas primeiras ~80 linhas
        # Upper bound = 500 porque a regiao TARGET no SEL-411L tem ate ~488 bytes
        # (3004h..31f7h = 500 rows). Manual pag. 10.5: TARGET char[~488].
        for row in range(0, 500):
            if row in self.layout.row_to_names:
                if self.layout.row_to_names[row] == names:
                    return row
                continue
            resp = self._send_ascii(f"TAR {row}".encode(), wait=0.4)
            row_names = self._parse_tar_header(resp)
            if row_names is None:
                continue
            self.layout.row_to_names[row] = row_names
            if row_names == names:
                return row
        return None

    # --- leitura: lê todos os 500 bytes de uma vez via VIEW 1:TARGET ---------

    def read_raw_bytes(self) -> bytes:
        """Envia VIEW 1:TARGET e retorna os ~500 bytes da Relay Word."""
        resp = self._send_ascii(b"VIEW 1:TARGET", wait=0.0,
                                quiet_period=0.1, deadline_s=2.0)
        match = _RE_TARGET_BYTES.search(resp)
        if not match:
            raise ValueError(
                f"Resposta inesperada de VIEW 1:TARGET (sem 'TARGET ='): "
                f"{resp[:200]!r}"
            )
        hex_section = match.group(1)
        bytes_list = [int(m.group(1), 16) for m in _RE_HEX_BYTE.finditer(hex_section)]
        return bytes(bytes_list)

    def read(self, bit_names: list[str]) -> dict[str, int | None]:
        """
        Le os bits pedidos. Escolhe automaticamente:
          - `TAR <bit>` por bit, se len <= tar_threshold (mais rapido)
          - `VIEW 1:TARGET` (le 500 bytes de uma vez) caso contrario
        """
        if 0 < len(bit_names) <= self.tar_threshold:
            return self.read_via_tar(bit_names)
        return self.read_via_view(bit_names)

    def read_via_view(self, bit_names: list[str]) -> dict[str, int | None]:
        """Le o TARGET inteiro via VIEW e extrai os bits pedidos."""
        unknown = [b for b in bit_names if b.upper() not in self.layout.bit_to_pos]
        if unknown:
            self.discover_bits(unknown)

        raw = self.read_raw_bytes()
        result: dict[str, int | None] = {}
        for name in bit_names:
            pos = self.layout.bit_to_pos.get(name.upper())
            if pos is None:
                result[name] = None
                continue
            row_idx, msb_bit = pos
            if row_idx >= len(raw):
                result[name] = None
                continue
            byte_val = raw[row_idx]
            result[name] = int(bool(byte_val & (1 << msb_bit)))
        return result

    def read_via_tar(self, bit_names: list[str]) -> dict[str, int | None]:
        """
        Le cada bit individualmente via `TAR <bit>` -- ~120ms por bit, mas
        nao requer parsing dos 500 bytes do TARGET.

        Recomendado apenas quando pipeline esta desligado e tem 1 bit; em
        outros casos use o caminho pipeline (FM+VIEW em 1 round-trip).
        """
        result = {}
        for name in bit_names:
            bit_upper = name.upper()
            # quiet_period maior pois o relé pode pausar entre header e valores
            resp = self._send_ascii(
                f"TAR {bit_upper}".encode(),
                wait=0.0, quiet_period=0.12, deadline_s=1.5,
            )
            value = self._parse_tar_value(resp, bit_upper)
            result[name] = value
        return result

    @staticmethod
    def _parse_tar_row(resp: bytes) -> tuple[list[str], list[bytes]] | None:
        """Extrai `(nomes, valores)` de uma resposta de TAR.

        Tanto `TAR <bit>` quanto `TAR <linha>` devolvem a MESMA coisa: a linha
        inteira de 8 bits, com uma linha de nomes e uma de valores 0/1. E' isso
        que permite ler 8 bits por round-trip em vez de 1.
        """
        header = None
        values = None
        for line in resp.split(b"\r"):
            line = line.strip()
            if not line:
                continue
            tokens = line.split()
            if len(tokens) != 8:
                continue
            if all(tok in (b"0", b"1") for tok in tokens):
                values = tokens
            elif header is None:
                header = [t.decode("utf-8", errors="replace").upper() for t in tokens]
        if header is None or values is None:
            return None
        return header, values

    def _parse_tar_value(self, resp: bytes, bit_name: str) -> int | None:
        """Extrai o valor de um bit especifico da resposta de TAR <bit>."""
        parsed = self._parse_tar_row(resp)
        if parsed is None:
            return None
        header, values = parsed
        try:
            idx = header.index(bit_name)
        except ValueError:
            return None
        return int(values[idx] == b"1")

    def read_via_tar_rows(self, bit_names: list[str]) -> dict[str, int | None]:
        """Le os bits pedidos agrupando-os por LINHA da Relay Word.

        Caminho para reles sem regiao TARGET acessivel (3xx): o SEL-311C
        responde `Invalid Command` a `VIEW 1:TARGET` e a `MAP 1 TARGET BL`,
        mas responde `TAR <linha>` normalmente -- e cada resposta traz os 8
        bits da linha. Agrupando os bits pedidos por linha, o custo cai de um
        round-trip por BIT (`read_via_tar`) para um por LINHA OCUPADA.
        """
        # Exclui os que ja tentamos e nao existem na Relay Word (equacoes
        # SELOGIC, display points, etc.). Sem isso o poll refaz a lista de
        # descoberta a cada volta so pra ela ser descartada de novo.
        unknown = [
            b for b in bit_names
            if b.upper() not in self.layout.bit_to_pos
            and b.upper() not in self.layout.not_findable
        ]
        if unknown:
            self.discover_bits(unknown)

        by_row: dict[int, list[str]] = {}
        result: dict[str, int | None] = {}
        for name in bit_names:
            pos = self.layout.bit_to_pos.get(name.upper())
            if pos is None:
                result[name] = None
                continue
            by_row.setdefault(pos[0], []).append(name)

        for row_idx in sorted(by_row):
            parsed = None
            # Uma linha ocasionalmente volta truncada (o rele pausa entre o
            # cabecalho e os valores e o quiet_period fecha a leitura antes).
            # Uma repeticao com folga maior resolve, e so custa quando falha.
            for attempt, (quiet, deadline) in enumerate(((0.12, 1.5), (0.30, 2.5))):
                resp = self._send_ascii(
                    f"TAR {row_idx}".encode(),
                    wait=0.0, quiet_period=quiet, deadline_s=deadline,
                )
                parsed = self._parse_tar_row(resp)
                if parsed is not None:
                    break
                if self.logger is not None and attempt == 0:
                    self.logger.debug("  TAR %d veio incompleto; repetindo", row_idx)
            if parsed is None:
                for name in by_row[row_idx]:
                    result[name] = None
                continue
            header, values = parsed
            for name in by_row[row_idx]:
                try:
                    idx = header.index(name.upper())
                except ValueError:
                    result[name] = None
                    continue
                result[name] = int(values[idx] == b"1")
        return result

    def read_all_active(self) -> dict[str, int]:
        """
        Le TODOS os bits da Relay Word e retorna apenas os ativos (=1).
        Requer discover_all_rows() previo.
        """
        raw = self.read_raw_bytes()
        active = {}
        for row_idx, names in self.layout.row_to_names.items():
            if row_idx >= len(raw):
                continue
            byte_val = raw[row_idx]
            for i, name in enumerate(names):
                if name == "*":
                    continue
                msb_bit = 7 - i
                if byte_val & (1 << msb_bit):
                    active[name] = 1
        return active


# =============================================================================
# Caminho binario (NAO IMPLEMENTADO -- documentacao SEL ausente)
# =============================================================================

class BinaryTargetReader:
    """
    Stub para o caminho binario via SEL Fast Message protocol (A546h).

    O cabecalho A546h e os Function Codes (30h/31h/33h/10h) estao listados
    no manual SEL-400 (Tabela 15.23), mas o formato completo do frame
    (comprimento total, layout do payload, calculo de checksum, ordem de
    bytes, sequenciamento, etc.) NAO esta no manual publico. O proprio
    manual diz na pagina 15.34:

        "you will also need to contact SEL for Fast Message protocol details"

    Tentamos sondar o rele com varios formatos plausiveis (A5 46 06 30 ...,
    A5 46 05 30 ..., etc.) e o rele apenas ecoou bytes sem responder. Sem
    a documentacao oficial, qualquer implementacao seria por adivinhacao e
    nao funcionaria de forma confiavel.

    Alternativas para acesso binario eficiente:
      - Solicitar a especificacao Fast Message a SEL diretamente
      - Usar DNP3 (descrito na Secao 16 do manual SEL-400)
      - Usar IEC 61850 GOOSE/MMS se disponivel no rele

    Para uso pratico, o AsciiTargetReader e suficiente: a regiao TARGET
    atualiza a cada 0.5s, e VIEW 1:TARGET retorna 500 bytes de uma vez
    (1 round-trip por leitura).
    """

    def __init__(self, client, logger=None):
        self.client = client
        self.logger = logger
        self._channel = None

    def read(self, bit_names: list[str]) -> dict:
        raise NotImplementedError(
            "Acesso binario via SEL Fast Message (A546h) nao e suportado.\n"
            "O formato do frame nao esta documentado publicamente -- o manual\n"
            "do rele instrui a 'contact SEL for Fast Message protocol details'.\n"
            "\n"
            "Use o caminho ASCII (AsciiTargetReader) em vez disso. Ele\n"
            "tambem cumpre a taxa de atualizacao de 0.5s da regiao TARGET\n"
            "e ja esta implementado e testado."
        )

    def read_all_active(self) -> dict:
        return self.read([])


# =============================================================================
# Factory
# =============================================================================

def get_target_reader(client, mode: str = "ascii", logger=None):
    """Retorna o reader apropriado ('ascii' ou 'binary')."""
    mode = (mode or "ascii").strip().lower()
    if mode == "binary":
        return BinaryTargetReader(client, logger=logger)
    return AsciiTargetReader(client, logger=logger)
