"""Caminhos canonicos do projeto.

Toda a app deve resolver paths a partir das constantes deste modulo --
nunca via `Path(__file__).parent` em cada arquivo. Assim, se o layout
mudar, so este arquivo precisa ser editado.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

# Diretorio do PACOTE. Tudo o que viaja DENTRO dele -- os templates .html, as
# fontes .woff2 -- sai daqui, e nao da raiz do projeto: sob `src/` (e mais
# ainda depois de um `pip install`) o pacote e a raiz deixaram de ser vizinhos.
PACKAGE_DIR: Path = Path(__file__).resolve().parent


def _find_project_root() -> Path:
    """A raiz do PROJETO: onde ficam `data/`, `config/`, `cache/`, `rdbs/`.

    Nao e' mais "o pai do pacote". Com o layout `src/`, o pai e' `src/`; num
    `pip install`, e' o `site-packages`. Entao procuramos o marcador -- um
    diretorio que tenha `data/` e `config/` -- subindo a partir do pacote, e
    `PACCT_ROOT` no ambiente vence tudo (e' o que a instalacao versionada da
    Fase 5 usa, com o `current/` apontando pra uma versao e os dados do
    engenheiro fora dela).
    """
    env = os.environ.get("PACCT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # O marcador e' `config/config.ini.example` mais o `app.py`: os dois
    # existem em toda instalacao e em todo clone. NAO e' mais `data/` -- os
    # registros por modelo mudaram de dono para a biblioteca `selfiles`, e a
    # pasta que sobrou aqui e' um overlay que comeca inexistente.
    for parent in PACKAGE_DIR.parents:
        if (parent / "config" / "config.ini.example").is_file() \
                and (parent / "app.py").is_file():
            return parent
    return PACKAGE_DIR.parent


PROJECT_ROOT: Path = _find_project_root()

# Onde mora o estado MUTAVEL (config.ini com as senhas, cache, rdbs). Separado
# da raiz porque uma instalacao versionada troca de versao a cada atualizacao e
# esses arquivos nao podem viajar junto: `PACCT_DATA_DIR` os deixa fora.
DATA_ROOT: Path = Path(
    os.environ.get("PACCT_DATA_DIR") or PROJECT_ROOT
).expanduser().resolve()

# Configuracao (config.ini, etc.). O `config.ini` NAO e' versionado -- e' onde
# se digitam as senhas ACC/2AC reais do rele, e uma senha de subestacao
# commitada fica no historico pra sempre. Quem vai pro git e' o `.example`, e a
# app semeia um a partir do outro no primeiro boot (`ensure_config_file`).
CONFIG_DIR: Path = DATA_ROOT / "config"
DEFAULT_CONFIG_FILE: Path = CONFIG_DIR / "config.ini"
EXAMPLE_CONFIG_FILE: Path = CONFIG_DIR / "config.ini.example"

# OVERLAY de dados por modelo. Os registros (perfis de rele, nomes validos da
# Relay Word, tabelas bit -> item MMS) sao da BIBLIOTECA `selfiles` e viajam
# dentro dela: sao conhecimento sobre reles, nao configuracao desta app. O que
# mora aqui e' o que o USUARIO acrescentou em tempo de execucao -- o "Importar
# perfil DNP" do editor de mapa grava um `wordbits/<MODELO>.json` aqui --, e o
# overlay e' procurado ANTES do que vem empacotado, por modelo. Comeca
# inexistente numa instalacao nova, e isso e' o estado normal.
#
# Fica sob DATA_ROOT (e nao sob a raiz do projeto) porque e' dado do
# engenheiro: uma atualizacao troca a versao instalada e nao pode levar junto
# o perfil que ele importou.
DATA_DIR: Path = DATA_ROOT / "data"
RELAY_MODELS_DIR: Path = DATA_DIR / "relay_models"
WORDBITS_DIR: Path = DATA_DIR / "wordbits"
MMS_MAP_DIR: Path = DATA_DIR / "mms_map"

# Estaticos servidos pelo dispatcher web em /static/ (fontes .woff2 embarcadas,
# licencas e NOTICE). Ficam no projeto de proposito: a subestacao pode nao ter
# internet, e nenhuma pagina deve pedir fonte a CDN.
STATIC_DIR: Path = PACKAGE_DIR / "web" / "static"

# Cache local (descoberta de bits TARGET, dumps de diagnostico, anotacoes
# do dashboard). Conteudo nao versionado.
CACHE_DIR: Path = DATA_ROOT / "cache"

# Extracoes de RDB, chaveadas pelo sha256 do arquivo. Dois arquivos iguais SAO
# o mesmo arquivo, entao a extracao e' unica no processo e sobrevive ao
# restart -- diferente de cache/sessions/, que e' apagado no boot.
RDB_CACHE_DIR: Path = CACHE_DIR / "rdb"

# Templates HTML do GLV. Sao arquivos .html de verdade (e nao string no .py)
# porque ~1.400 das 2.500 linhas sao JavaScript: assim o editor colore e o
# linter enxerga. A mecanica de substituicao ("${PAGES_JSON}") nao mudou.
GLV_TEMPLATES_DIR: Path = PACKAGE_DIR / "web" / "glv" / "templates"

# HTML templates for the DNP map editor. Same reason as the GLV: real .html
# files because most of it is JavaScript.
DNP_TEMPLATES_DIR: Path = PACKAGE_DIR / "web" / "dnp_map" / "templates"

# HTML template for the Arquivos do Projeto tab. Same reason as the GLV and
# the DNP map: a real .html file, because most of it is JavaScript.
PROJECT_FILES_TEMPLATES_DIR: Path = (
    PACKAGE_DIR / "web" / "project_files" / "templates"
)

# HTML template for the VLAN Mapper. Same reason as the GLV: a real .html file,
# because most of it is JavaScript.
VLAN_MAPPER_TEMPLATES_DIR: Path = (
    PACKAGE_DIR / "web" / "vlan_mapper" / "templates"
)

# HTML template for the GLE Variable Comment Exporter. Same reason as the GLV:
# a real .html file, because most of it is JavaScript.
GLE_EXPORTER_TEMPLATES_DIR: Path = (
    PACKAGE_DIR / "web" / "gle_exporter" / "templates"
)

# HTML templates for the VB Updater. Same reason as the GLV: real .html files,
# because most of those lines are JavaScript.
VB_UPDATER_TEMPLATES_DIR: Path = (
    PACKAGE_DIR / "web" / "vb_updater" / "templates"
)

# HTML template for the Settings Compare. Same reason as the GLV, and the
# starkest case of it: the diff UI is ~1.000 lines, nearly all JavaScript.
SETTINGS_COMPARE_TEMPLATES_DIR: Path = (
    PACKAGE_DIR / "web" / "settings_compare" / "templates"
)

# Uploads de RDB feitos via landing page do dashboard.
RDBS_DIR: Path = DATA_ROOT / "rdbs"

# Amostras de GLE / RDB / SCD que acompanham o repositorio (uso de
# desenvolvimento / teste).
SAMPLES_DIR: Path = PROJECT_ROOT / "samples"

# Documentacao (manuais SEL, application guides).
DOCS_DIR: Path = PROJECT_ROOT / "docs"

# Insumos e relatorios de analise que nao sao codigo nem amostra: os ICD/CID de
# fabrica, os perfis DNP e os .txt que `tools/` gera a partir deles. Ficam fora
# de `samples/` porque nao sao entrada de teste -- sao a materia-prima e o
# resultado das analises de cobertura.
FIXTURES_DIR: Path = PROJECT_ROOT / "fixtures"


def ensure_dirs() -> None:
    """Cria diretorios mutaveis (cache, rdbs) se ainda nao existirem."""
    for d in (CACHE_DIR, RDB_CACHE_DIR, RDBS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def is_within(target: Path, roots) -> bool:
    """Diz se `target` esta dentro de algum diretorio de `roots`.

    Usado pelo sandbox dos endpoints `/download` (anti path traversal).
    Compara via `Path.relative_to`, e nao concatenando `"/"` -- no Windows
    o separador e `\\`, e a comparacao textual rejeitaria todo caminho
    valido (403 em qualquer download).
    """
    target = Path(target).resolve()
    for root in roots:
        try:
            target.relative_to(Path(root).resolve())
        except ValueError:
            continue
        return True
    return False


def resolve_gle_path(value: str) -> Path:
    """Resolve um caminho de GLE vindo do config.ini ou da CLI.

    Ordem de tentativa:
      1. Caminho absoluto (retorna como esta).
      2. Relativo a `PROJECT_ROOT` (compatibilidade com config antigo).
      3. Relativo a `SAMPLES_DIR` (default da nova estrutura).
    """
    p = Path(value)
    if p.is_absolute():
        return p
    candidate_root = PROJECT_ROOT / p
    if candidate_root.exists():
        return candidate_root.resolve()
    return (SAMPLES_DIR / p).resolve()


def ensure_config_file(path: Path, logger: logging.Logger | None = None) -> bool:
    """Garante que o arquivo de configuracao exista, semeando do `.example`.

    `config/config.ini` e' gitignored (e' o arquivo onde vao as senhas reais
    do rele), entao um clone limpo simplesmente nao tem um. Sem isto, a app
    subia lendo um .ini inexistente e caia nos fallbacks de cada
    `cfg.get(...)`, silenciosamente -- que e' o pior jeito de errar.

    Devolve True se copiou o modelo, False se o arquivo ja existia. Levanta
    `FileNotFoundError` se nem o arquivo nem um modelo existirem: a app nao
    tem o que ler e dizer isso alto e' melhor que adivinhar.
    """
    path = Path(path)
    if path.is_file():
        return False

    # Modelo irmao do proprio caminho pedido (cobre `--config outro.ini`), com
    # o config/config.ini.example do projeto como ultimo recurso.
    candidates = [Path(str(path) + ".example"), EXAMPLE_CONFIG_FILE]
    candidates = list(dict.fromkeys(candidates))
    for example in candidates:
        if example.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(example, path)
            (logger or logging.getLogger(__name__)).info(
                "Configuracao ausente: %s criado a partir de %s. "
                "Edite-o com o IP e as senhas do rele (ele nao vai pro git).",
                path, example,
            )
            return True

    raise FileNotFoundError(
        f"Arquivo de configuracao nao encontrado: {path}. Nao ha modelo para "
        f"copiar ({', '.join(str(c) for c in candidates)}). Restaure "
        f"config/config.ini.example do repositorio ou informe --config."
    )
