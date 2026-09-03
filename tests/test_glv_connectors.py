"""Conectores do GLE: a rede nomeada que o desenho usa no lugar de uma linha.

Um conector NAO e' uma aresta no XML. Sao dois ou mais
`<element type="Connector">` que compartilham um `<label>`, e nada os liga:

    conn 658:  elemento 667    ->  Connector #723   <label>Cont1</label>
    conn 134:  Connector #206  ->  elemento 517     <label>Cont1</label>

Medido nos 418 `.gle` de `rdbs/extracted/`: 107 usam conector, 324 elementos,
110 redes -- e **exatamente um receptor por rede, 110 de 110**, com leque de
saida ate 9 e 9 redes atravessando pagina. Nenhum GLE versionado tem conector,
por isso a fixture reproduz essas formas em vez de depender de um RDB.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from selfiles.gle import parse_gle

from pacct.web.glv import connectors

FIXTURE = Path(__file__).parent / "fixtures" / "connectors.gle.xml"


@pytest.fixture(scope="module")
def nets():
    return connectors.extract(parse_gle(FIXTURE))


@pytest.fixture(scope="module")
def svg():
    from selfiles.gle import render_page
    return render_page(parse_gle(FIXTURE).findall(".//page")[0])


class TestTheNetItself:

    def test_a_label_becomes_one_net_with_one_receiver(self, nets):
        assert nets["Cont1"].receiver == "4"
        assert nets["Cont1"].emitters == ("5",)

    def test_the_driver_page_is_where_the_receiver_sits(self, nets):
        assert nets["LEDGRP"].driver_page == "P1"
        assert nets["LEDGRP"].pages == ("P1", "P2_LEDs")

    def test_a_net_can_fan_out_to_several_emitters(self, nets):
        """Medido: 12 redes do corpus tem 9 emissores."""
        assert nets["LEDGRP"].emitters == ("14", "15")

    def test_a_label_with_no_receiver_is_not_a_net(self, nets):
        """Adivinhar qual ponta aciona pintaria linha por chute."""
        assert "SEMFONTE" not in nets


class TestTheEquation:

    def test_it_reads_back_in_selogic(self, nets):
        """`*` E, `+` OU, `!` NAO -- a notacao do AcSELerator. Medido: nenhum
        bloco aritmetico aparece dentro de equacao de conector em todo o
        corpus, entao `*` e `+` nao colidem com ADD/MULT."""
        assert nets["Cont1"].equation == "(IN101 * !IN102)"

    def test_the_leaf_bits_are_what_the_poll_has_to_ask_for(self, nets):
        assert nets["Cont1"].bits == frozenset({"IN101", "IN102"})
        assert nets["LEDGRP"].bits == frozenset({"GROUNDT", "TRIPS"})

    def test_an_or_reads_back_with_plus(self, nets):
        assert nets["LEDGRP"].equation == "(GROUNDT + TRIPS)"

    def test_the_tree_is_json_shaped_for_the_client(self, nets):
        """Python extrai ESTRUTURA; o JS decide SEMANTICA. Por isso a arvore
        viaja como dado, e nao como um valor ja avaliado -- uma segunda
        implementacao de NOT/RTRIG/latch em Python e' exatamente o tipo de
        copia que este repositorio ja viu divergir."""
        import json
        t = nets["Cont1"].tree
        assert json.loads(json.dumps(t)) == t
        assert t["op"] == "AND"
        assert t["args"][0] == {"op": "BIT", "name": "IN101"}
        assert t["args"][1] == {"op": "NOT",
                                "args": [{"op": "BIT", "name": "IN102"}]}


class TestItTerminates:

    def test_a_feedback_loop_ends_instead_of_hanging(self, nets):
        """Um extrator que entra em laco trava o `build_diagram` inteiro. O
        corpus nao tem ciclo nenhum (0 em 110), mas realimentacao de latch
        teria."""
        assert "LACO" in nets
        assert "IN109" in nets["LACO"].bits

    def test_the_cut_is_visible_instead_of_invented(self, nets):
        """Uma equacao visivelmente incompleta e' honesta; uma inventada
        nao."""
        assert "…" in nets["LACO"].equation


class TestConstants:
    """Um literal do desenho e' um valor, nao um bit e nao um corte.

    Medido: o unico corte que sobrava no corpus inteiro era o preset de um
    contador -- `PCN((PLT16 * ↓PCT17Q), …, (↑PSV12 + PCN01Q + PSV60))`, onde
    o `…` era a constante `6` na porta do meio. Constante nao existe na Relay
    Word (`collect_bit_names` ja a exclui), entao ela nao entra no polling --
    mas mostrar `…` no lugar dela diz que a equacao esta incompleta quando
    nao esta.
    """

    def test_a_literal_reads_back_as_its_value(self, nets):
        assert nets["PRESET"].equation == "(IN110 * 6)"

    def test_a_literal_is_not_a_bit_to_poll(self, nets):
        assert nets["PRESET"].bits == frozenset({"IN110"})

    def test_the_client_gets_it_as_a_number(self, nets):
        assert nets["PRESET"].tree["args"][1] == {"op": "CONST", "value": 6.0}


# -- o desenho: o conector tem que dizer o nome dele --------------------------

class TestRendering:
    """Sem o label no SVG nao ha chave de pareamento -- e nem como uma pessoa
    ver que as duas pontas sao a mesma rede.

    O conector saia como uma caixa com `→` porque `render_gate` recebe o
    `info` de `element_info`, que le nome de `<logic_element>`; um Connector
    nao tem `logic_element` nenhum, o `<label>` e' filho direto do
    `<element>`.
    """

    def test_the_group_carries_the_label_as_the_pairing_key(self, svg):
        assert 'id="el-4" data-type="Connector" data-connector="Cont1"' in svg
        assert 'id="el-5" data-type="Connector" data-connector="Cont1"' in svg

    def test_the_box_shows_the_name_instead_of_an_anonymous_arrow(self, svg):
        assert ">Cont1<" in svg
        assert ">LEDGRP<" in svg

    def test_a_label_with_xml_special_characters_is_escaped(self):
        """Os labels reais sao livres -- `TC - LOP`, `RX 50BF C/ BARRAS
        UNIDAS`. Um `&` num label nao pode quebrar o SVG."""
        import xml.etree.ElementTree as ET

        from selfiles.gle import element_info, render_element
        el = ET.fromstring(
            '<element id="9" type="Connector" left="0" top="0">'
            '<label>A &amp; B</label></element>')
        out = render_element(element_info(el), 1, 1)
        assert 'data-connector="A &amp; B"' in out
        assert ">A &amp; B<" in out


# -- o diagrama: a rede tem que chegar ao polling e ao cliente ---------------

def _diagram():
    """Um `GlvDiagram` da fixture, sem tocar em rede (build_diagram nao toca).

    `relay_model=None` de proposito: a fixture nao e' de modelo nenhum, e o
    caminho sem modelo tem que funcionar -- e' o que acontece com um relé cujo
    perfil ninguem escreveu ainda.
    """
    import logging

    from pacct.web.glv.diagram import build_diagram
    return build_diagram("d1", FIXTURE, "REL", "conn.gle", "192.0.2.10", 23,
                         None, logging.getLogger("test"))


class TestTheDiagramCarriesTheNets:

    def test_build_extracts_them_without_touching_the_network(self):
        d = _diagram()
        assert set(d.connectors) == {"Cont1", "LEDGRP", "LACO", "PRESET"}

    def test_the_open_page_polls_the_bits_that_drive_its_connectors(self):
        """`LEDGRP` e' acionada em P1 e derivada em P2. Abrir P2 tem que pedir
        GROUNDT e TRIPS, que nao sao desenhados em P2 -- sem isso o conector
        fica indeterminado para sempre, que e' o caso `LEDGROUND` medido no
        corpus (9 redes atravessando pagina)."""
        d = _diagram()
        d.values("P2_LEDs")
        assert {"GROUNDT", "TRIPS"} <= d.idle.wanted_bits

    def test_the_page_bits_are_still_there_alongside_them(self):
        d = _diagram()
        d.values("P2_LEDs")
        assert {"LED05", "LED06"} <= d.idle.wanted_bits

    def test_the_driving_bits_were_already_in_the_map_request(self):
        """Nao muda `ensure_bits`: os bits ja entram em `all_wanted_bits`
        porque estao no GLE, em alguma pagina. O que faltava era o filtro por
        pagina aberta os estreitar de volta."""
        d = _diagram()
        assert {"GROUNDT", "TRIPS"} <= d.all_wanted_bits

    def test_values_hands_the_page_connectors_to_the_client(self):
        d = _diagram()
        payload = d.values("P2_LEDs")
        assert set(payload["connectors"]) == {"LEDGRP"}
        net = payload["connectors"]["LEDGRP"]
        assert net["equation"] == "(GROUNDT + TRIPS)"
        assert net["tree"]["op"] == "OR"
        assert sorted(net["bits"]) == ["GROUNDT", "TRIPS"]

    def test_a_page_with_no_connector_says_so_with_an_empty_dict(self):
        d = _diagram()
        d.connectors = {}
        assert d.values("P1")["connectors"] == {}
