"""Ajustes de conexao com o rele, e a costura com o `selprotopy` vendorizado.

=============================================================================
CONTRATO COM O `selprotopy` -- leia antes de atualizar a lib vendorizada
=============================================================================

`selprotopy/` e' vendorizado e patchado (um hook de PreToolUse bloqueia edicao
la dentro), entao TODO acoplamento com os nomes privados dele mora neste
arquivo, dentro de `FastMessageChannel`. Se um resync do upstream quebrar
alguma coisa, e' aqui -- e so' aqui -- que se conserta.

O que o toolkit usa de um `SELClient`, e o que cada coisa e':

  PRIVADO (comeca com "_", pode sumir num upgrade sem aviso):
    client._write(bytes)              manda bytes crus pro rele
    client._read_clean_prompt()       consome ate o prompt (usado no setup)
    client._read_command_response(c)  le a resposta de um comando ASCII
    client._read_to_prompt()          idem, sem filtrar pelo comando

  PUBLICO na pratica, mas nao documentado como API:
    client.conn                       o `telnetlib.Telnet` por baixo
    client.fm_command_1               marcador do Fast Meter (ex: b'\\xa5\\xd1')
    client.fm_config_command_1        marcador do A5C1 (config do Fast Meter)
    client.fast_meter_definition      layout dos analogicos, do autoconfig
    client.dnaDef                     nomes da Relay Word, do bloco DNA

  NAO E' DO `selprotopy` -- era um monkey-patch nosso:
    client._buffer_clean              "o buffer esta limpo no prompt?"

Esse ultimo merece a historia: os loops de poll penduravam esse atributo no
objeto do selprotopy e o `AsciiTargetReader` lia/escrevia o MESMO atributo --
era assim que os dois combinavam quem precisava drenar o buffer antes de
mandar o proximo comando. Um aperto de mao entre dois modulos atraves de um
campo inventado no objeto de um terceiro. Agora e' estado do
`FastMessageChannel`, e o aperto de mao continua valendo porque os dois lados
pegam o MESMO canal via `channel_for(client)`.

Quem NAO passou por aqui: `pacct/cli/runner.py` ainda fala direto com os
privados. E' o modo CLI, que roda sozinho contra um rele so' e nao divide
conexao com ninguem; migrar tambem seria bom, mas nao e' o que o Phase 6B
pedia e cada linha ali precisa de bancada pra verificar.
"""

from __future__ import annotations

import select
import time
import weakref

from selprotopy.protocol import commands


def wait_readable(conn, timeout: float) -> None:
    """Dorme ate o socket ter bytes, ou ate `timeout`. Nunca levanta.

    Substitui os `time.sleep(0.005)` / `time.sleep(0.01)` com que os loops de
    leitura esperavam a resposta do rele. O sleep fixo acorda a thread 100-200
    vezes por segundo haja dado ou nao; medido na bancada de Exemplo, 76%
    das leituras de socket de um SEL-411L voltavam vazias, e um SEL-311C
    chegava a 1913 leituras vazias em 20 s de polling.

    Vive aqui, e nao no `poll.py`, por duas razoes: `pacct/core/` e' o lado
    que ja fala com o socket do rele (`drain_login_banner` esta neste arquivo),
    e o outro caminho que precisava dela e' o `_send_ascii` do
    `core/target_region.py` -- se a funcao morasse em `web/glv/poll.py`, o
    core teria de importar da camada web pra usa-la.

    IMPORTANTE pra quem for mexer: chame isto SO depois de uma leitura que
    voltou vazia. `Telnet.read_very_eager()` primeiro drena a fila ja cozida
    (`cookedq`), entao pode haver dado disponivel para a aplicacao sem que o
    socket esteja legivel -- selecionar antes de ler faria a thread esperar
    por bytes que ja estao na mao.
    """
    if timeout <= 0:
        return
    try:
        sock = conn.get_socket()
    except Exception:
        sock = None
    if sock is None:
        # Sem socket (conexao fechada, ou um duble em teste): mantem o
        # comportamento antigo pra nao virar busy-loop puro.
        time.sleep(min(timeout, 0.005))
        return
    try:
        select.select([sock], [], [], timeout)
    except (OSError, ValueError):
        # fd fechado debaixo da thread -- volta e deixa a leitura seguinte
        # decidir o que fazer, exatamente como antes.
        return


def drain_login_banner(tn, logger=None, timeout: float = 8.0) -> bytes:
    """Consome o banner de login ate o prompt "=" aparecer.

    O `_verify_connection()` do selprotopy faz UM `read_until(b"\\r\\n")` por
    tentativa, com no maximo 5 tentativas (`__num_con_check__`) -- ou seja,
    ele atravessa no maximo 5 LINHAS de banner antes de desistir.

    Um rele com banner curto passa facil:

        b'TERMINAL SERVER:\\r\\n='                      -> acha "=" na 2a leitura

    Ja um SEL-311C anuncia nome do equipamento, data/hora, subestacao e
    modelo:

        b'TERMINAL SERVER\\r\\n\\x02\\r\\nQPC1-LT2-UPC2  Date: ... Time: ...\\r\\n'
        b'SE EXEMPLO\\r\\n\\r\\nSEL-311C\\r\\n\\x03\\x02\\r\\n=\\x03'

    ...e as 5 tentativas se esgotam ainda dentro do banner, sem nunca chegar
    no "=". A conexao falhava com `ConnVerificationFail` mesmo com o rele
    respondendo perfeitamente -- o que dependia do TAMANHO DO BANNER, nao do
    modelo nem da rede.

    Drenando o banner ANTES de construir o cliente, a verificacao dele comeca
    com o buffer limpo e passa em qualquer rele, com qualquer banner.

    A correcao vive aqui (e nao no `_verify_connection`) porque `selprotopy` e'
    vendorizado: um patch la seria perdido no proximo resync.
    """
    buf = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = tn.read_very_eager()
        except EOFError:
            break
        if chunk:
            buf += chunk
            if commands.LEVEL_0 in chunk:
                break
        else:
            # Nada pendente: cutuca com um CR pra provocar o prompt.
            tn.write(commands.CR)
            time.sleep(0.3)
    if logger is not None:
        if commands.LEVEL_0 in buf:
            logger.info("  banner de login drenado (%d bytes, prompt encontrado)",
                        len(buf))
        else:
            logger.warning(
                "  prompt '=' nao apareceu em %.0fs (%d bytes lidos); "
                "seguindo assim mesmo", timeout, len(buf))
    return buf


# =============================================================================
# O seam: a unica classe que conhece os nomes privados do selprotopy
# =============================================================================

# Um canal por cliente. WeakKeyDictionary e nao um atributo no cliente porque
# a ideia toda e' parar de escrever em objeto dos outros -- e assim o canal
# some junto com o cliente, sem prender nada vivo.
_CHANNELS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def channel_for(client) -> FastMessageChannel:
    """O canal DESTE cliente, criando na primeira vez.

    Tem de ser o mesmo objeto pros dois lados: o loop de poll e o
    `AsciiTargetReader` combinam entre si, atraves de `is_clean`, quem precisa
    drenar o buffer antes do proximo comando. Dois canais para uma conexao
    seriam dois palpites sobre o mesmo socket.
    """
    ch = _CHANNELS.get(client)
    if ch is None:
        ch = FastMessageChannel(client)
        _CHANNELS[client] = ch
    return ch


class FastMessageChannel:
    """Fala SEL Fast Message por cima de um `SELClient` do selprotopy.

    Existe pra que os tres `poll_loop*` e o `AsciiTargetReader` nao precisem
    conhecer `_write`, `conn`, `fm_command_1`, `dnaDef` nem `_buffer_clean`.
    Todo esse vocabulario esta listado no docstring do modulo.

    Nao tem lock: um canal pertence a UMA conexao telnet, e o `RelayLink` ja
    garante que so uma thread de polling fala nela por vez (`ensure_bits()`
    para a thread antes de rodar a descoberta justamente por isso).
    """

    __slots__ = ("_client", "_clean")

    def __init__(self, client):
        self._client = client
        # Comeca sujo: logo depois do login/autoconfig ainda ha eco de comando
        # ASCII no caminho, e a primeira volta do poll tem de drenar.
        self._clean = False

    # --- o que o rele expoe (vem do autoconfig do selprotopy) --------------

    @property
    def fm_marker(self) -> bytes:
        """Os bytes que abrem um frame Fast Meter (ex: b'\\xa5\\xd1').

        Serve pros dois lados: e' o que se ESCREVE pra pedir, e e' o que se
        PROCURA no stream pra achar o inicio da resposta.
        """
        return self._client.fm_command_1

    @property
    def fast_meter_definition(self):
        return self._client.fast_meter_definition

    @property
    def dna_definition(self):
        return self._client.dnaDef

    # --- estado do buffer -------------------------------------------------

    @property
    def is_clean(self) -> bool:
        """O socket esta parado no prompt, sem sobra de resposta anterior?"""
        return self._clean

    def mark_clean(self) -> None:
        self._clean = True

    def mark_dirty(self) -> None:
        self._clean = False

    # --- escrita ----------------------------------------------------------

    def send_fast_meter(self) -> None:
        """Pede um frame Fast Meter (A5D1)."""
        self._client._write(self._client.fm_command_1 + commands.CR)

    def send_target_region(self) -> None:
        """Pede a regiao TARGET inteira. So os 4xx respondem: num 311C isto
        volta `Invalid Command` (medido num 311C-1-R509)."""
        self._client._write(b"VIEW 1:TARGET\r\n")

    def send_ascii(self, cmd: bytes) -> None:
        """Manda um comando ASCII com CRLF (ex: b'TAR 13')."""
        self._client._write(cmd + b"\r\n")

    # --- leitura ----------------------------------------------------------

    def read_available(self) -> bytes:
        """Tudo que da' pra ler sem bloquear. b'' quando nao ha nada.

        Pode levantar `EOFError` com a conexao caida -- de proposito: os loops
        tratam isso no `except` deles e marcam o erro no LiveState, que e' o
        que aparece no cabecalho do diagrama.
        """
        return self._client.conn.read_very_eager()

    def wait_readable(self, timeout: float) -> None:
        """Dorme ate ter byte no socket, ou ate `timeout`."""
        wait_readable(self._client.conn, timeout)

    def drain(self, deadline_s: float = 0.3, settle_s: float = 0.02) -> None:
        """Come a sobra de uma resposta ASCII anterior.

        Nao e' busy-wait: o `sleep` curto so acontece quando AINDA veio dado, e
        o laco para na primeira leitura vazia.
        """
        if self._clean:
            return
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            if not self.read_available():
                time.sleep(settle_s)
                break
            time.sleep(0.005)
