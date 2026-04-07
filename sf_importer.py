"""
Screaming Frog SEO Spider — Importador de CSV e Integração CLI

Suporta duas formas de uso:
  1. Upload manual de CSV exportado pelo usuário
  2. Execução direta via CLI do Screaming Frog (uso local)

Colunas esperadas do CSV "Internal:All" do Screaming Frog:
  Address, Status Code, Content Type, Title 1, Title 1 Length,
  Meta Description 1, Meta Description 1 Length,
  H1-1, H1-2, H2-1, Word Count, Average Words Per Sentence,
  Response Time, Canonical Link Element 1, Unique Inlinks, Indexability
"""

import csv
import io
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode(content):
    """Decode bytes to string trying UTF-8 BOM → UTF-8 → latin-1."""
    if isinstance(content, str):
        return content
    for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode('latin-1', errors='replace')


def _parse_csv(content):
    """Parse CSV bytes/str into list of row dicts."""
    text = _decode(content)
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def _col(row, *candidates, default=''):
    """Return first non-empty value matching any candidate column name."""
    for name in candidates:
        val = row.get(name, '').strip()
        if val:
            return val
    return default


def _int(value, default=0):
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _float(value, default=0.0):
    try:
        return float(str(value).replace('s', '').replace(',', '.').strip())
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Issue generation from SF row data
# ---------------------------------------------------------------------------

def _issues_from_sf_row(row):
    """
    Generate SEO issues from a Screaming Frog CSV row.
    Returns (page_dict, issues_list).
    """
    url          = _col(row, 'Address', 'address', 'URL', 'url')
    status_code  = _int(_col(row, 'Status Code', 'Status code', 'Status'))
    title        = _col(row, 'Title 1', 'Title 1 (text)', 'Title')
    title_len    = _int(_col(row, 'Title 1 Length', 'Title Length'), len(title))
    meta         = _col(row, 'Meta Description 1', 'Meta Description 1 (text)', 'Meta Description')
    meta_len     = _int(_col(row, 'Meta Description 1 Length', 'Meta Description Length'), len(meta))
    h1_1         = _col(row, 'H1-1', 'H1 1', 'H1')
    h1_2         = _col(row, 'H1-2', 'H1 2')
    h2_1         = _col(row, 'H2-1', 'H2 1', 'H2')
    word_count   = _int(_col(row, 'Word Count', 'Words'))
    asl          = _float(_col(row, 'Average Words Per Sentence', 'Avg Words Per Sentence'))
    load_time    = _float(_col(row, 'Response Time', 'Load Time'))
    canonical    = _col(row, 'Canonical Link Element 1', 'Canonical Link Element', 'Canonical')
    inlinks      = _int(_col(row, 'Unique Inlinks', 'Inlinks', 'Internal Inlinks'))
    indexability = _col(row, 'Indexability', 'indexability')

    # SF exports response time in ms; convert if > 100
    if load_time > 100:
        load_time = round(load_time / 1000, 2)
    else:
        load_time = round(load_time, 2)

    page = {
        'url': url,
        'title': title,
        'status_code': status_code,
        'word_count': word_count,
        'load_time': load_time,
        # Keep SF metadata for AI suggestions later
        '_meta': meta,
        '_h1': h1_1,
        '_h2': h2_1,
    }

    issues = []

    # ---- HTTP Errors ----
    if status_code >= 400:
        issues.append({
            'category': 'technical', 'severity': 'high',
            'title': f'Erro HTTP {status_code}',
            'description': f'Esta página retornou o código {status_code}.',
            'current_value': str(status_code),
            'suggestion': 'Corrija o erro ou redirecione com status 301 para uma URL válida.'
        })
        return page, issues

    # ---- Noindex warning ----
    if indexability and indexability.lower() == 'non-indexable':
        issues.append({
            'category': 'technical', 'severity': 'medium',
            'title': 'Página não indexável (noindex)',
            'description': 'Esta página está bloqueada para indexação pelo Google.',
            'current_value': indexability,
            'suggestion': 'Verifique se o noindex é intencional. Se a página deve aparecer no Google, remova a diretiva noindex.'
        })

    # ---- Title ----
    if not title:
        issues.append({
            'category': 'title', 'severity': 'high',
            'title': 'Tag <title> ausente ou vazia',
            'description': 'O título da página é o principal fator de SEO on-page e aparece nos resultados do Google.',
            'current_value': '(sem título)',
            'suggestion': 'Adicione um título com 50–60 caracteres contendo a palavra-chave principal no início.'
        })
    elif title_len < 30:
        issues.append({
            'category': 'title', 'severity': 'medium',
            'title': f'Título muito curto ({title_len} caracteres)',
            'description': 'Títulos curtos não aproveitam o espaço nos resultados de busca.',
            'current_value': title,
            'suggestion': 'Expanda para 50–60 caracteres com a palavra-chave e nome da empresa.'
        })
    elif title_len > 60:
        issues.append({
            'category': 'title', 'severity': 'medium',
            'title': f'Título muito longo ({title_len} caracteres)',
            'description': 'O Google trunca títulos com mais de ~60 caracteres.',
            'current_value': title,
            'suggestion': 'Reduza para no máximo 60 caracteres mantendo as palavras-chave principais.'
        })

    # ---- Meta Description ----
    if not meta:
        issues.append({
            'category': 'meta', 'severity': 'high',
            'title': 'Meta description ausente',
            'description': 'A meta description influencia diretamente o CTR nos resultados de busca.',
            'current_value': '(sem meta description)',
            'suggestion': 'Adicione 150–160 caracteres com uma descrição atrativa e call-to-action.'
        })
    elif meta_len < 120:
        issues.append({
            'category': 'meta', 'severity': 'medium',
            'title': f'Meta description muito curta ({meta_len} caracteres)',
            'description': 'Uma meta description curta desperdiça espaço nos resultados de busca.',
            'current_value': meta,
            'suggestion': 'Expanda para 150–160 caracteres com texto persuasivo.'
        })
    elif meta_len > 160:
        issues.append({
            'category': 'meta', 'severity': 'low',
            'title': f'Meta description muito longa ({meta_len} caracteres)',
            'description': 'O Google corta após ~160 caracteres.',
            'current_value': meta,
            'suggestion': 'Reduza para no máximo 160 caracteres.'
        })

    # ---- H1 ----
    if not h1_1:
        issues.append({
            'category': 'heading', 'severity': 'high',
            'title': 'Tag H1 ausente',
            'description': 'O H1 informa ao Google o tema central da página.',
            'current_value': '(sem H1)',
            'suggestion': 'Adicione um único H1 claro e descritivo com a palavra-chave principal.'
        })
    elif h1_2:
        issues.append({
            'category': 'heading', 'severity': 'medium',
            'title': 'Múltiplos H1 encontrados',
            'description': 'Cada página deve ter apenas um H1.',
            'current_value': f'{h1_1}  |  {h1_2}',
            'suggestion': 'Mantenha apenas 1 H1. Converta os demais em H2 ou H3.'
        })

    # ---- H2 ----
    if not h2_1 and h1_1:
        issues.append({
            'category': 'heading', 'severity': 'low',
            'title': 'Nenhum subtítulo H2 encontrado',
            'description': 'Subtítulos H2 organizam o conteúdo e melhoram o SEO.',
            'current_value': '0 H2',
            'suggestion': 'Divida o conteúdo em seções com H2 descritivos contendo palavras-chave secundárias.'
        })

    # ---- Content length ----
    if 0 < word_count < 300:
        issues.append({
            'category': 'content', 'severity': 'medium',
            'title': f'Conteúdo escasso ({word_count} palavras)',
            'description': 'Páginas com pouco texto têm dificuldade de ranquear.',
            'current_value': f'{word_count} palavras',
            'suggestion': 'Adicione descrição detalhada, benefícios, FAQ e depoimentos.'
        })

    # ---- Flesch PT-BR (estimado com ASL do SF + heurística de sílabas PT) ----
    # O SF fornece o ASL (Média de Palavras por Frase).
    # Para o ASW usamos a média do português: ~2.45 sílabas/palavra (Biderman 2001).
    # Fórmula: 248.835 - 1.015×ASL - 84.6×ASW  (Martins et al. 1996)
    if word_count >= 50 and asl > 0:
        asw_pt = 2.45
        flesch = round(max(0.0, min(100.0, 248.835 - (1.015 * asl) - (84.6 * asw_pt))), 1)
        nivel = (
            'Muito Fácil' if flesch >= 75 else
            'Fácil'       if flesch >= 50 else
            'Difícil'     if flesch >= 25 else
            'Muito Difícil'
        )
        if flesch < 25:
            sev = 'high'
        elif flesch < 50:
            sev = 'medium'
        elif flesch < 60:
            sev = 'low'
        else:
            sev = None

        if sev:
            issues.append({
                'category': 'legibilidade', 'severity': sev,
                'title': f'Legibilidade — Flesch {flesch} ({nivel})',
                'description': (
                    f'Índice de Flesch PT-BR estimado: {flesch}/100 — "{nivel}". '
                    f'Calculado com base nos dados do Screaming Frog: '
                    f'{asl} palavras/frase (ASL) e média de {asw_pt} sílabas/palavra.'
                ),
                'current_value': f'Flesch ≈ {flesch} | {nivel} | ASL {asl}',
                'suggestion': (
                    'Use frases mais curtas (ideal: até 20 palavras) e palavras do cotidiano. '
                    'Divida parágrafos longos e adicione subtítulos H2.'
                )
            })

    # ---- Canonical ----
    if not canonical:
        issues.append({
            'category': 'technical', 'severity': 'low',
            'title': 'Tag canonical ausente',
            'description': 'Evita penalidades por conteúdo duplicado.',
            'current_value': '(sem canonical)',
            'suggestion': f'Adicione: <link rel="canonical" href="{url}">'
        })

    # ---- Load time ----
    if load_time > 3:
        issues.append({
            'category': 'technical', 'severity': 'medium',
            'title': f'Carregamento lento ({load_time}s)',
            'description': 'O Google recomenda menos de 3 segundos.',
            'current_value': f'{load_time}s',
            'suggestion': 'Otimize imagens, use cache e considere um CDN.'
        })

    # ---- Internal inlinks ----
    if inlinks < 3:
        issues.append({
            'category': 'links', 'severity': 'low',
            'title': f'Poucos links internos apontando para esta página ({inlinks})',
            'description': 'Páginas com poucos inlinks têm menor autoridade interna.',
            'current_value': f'{inlinks} inlink(s)',
            'suggestion': 'Adicione links de outras páginas relevantes do site para esta.'
        })

    return page, issues


# ---------------------------------------------------------------------------
# Process uploaded CSV files
# ---------------------------------------------------------------------------

def process_sf_csv(internal_csv, images_csv=None):
    """
    Process Screaming Frog exported CSV files.

    Args:
        internal_csv: bytes or str — export "Internal:All"
        images_csv:   bytes or str — export "Images:Missing Alt Text" (opcional)

    Returns:
        list of (page_dict, issues_list)
    """
    rows = _parse_csv(internal_csv)

    # Build missing-alt map from images CSV
    missing_alt = {}   # url -> count
    if images_csv:
        try:
            for row in _parse_csv(images_csv):
                page_url = _col(row, 'From', 'Source', 'Page', 'From (Href)', 'from')
                if page_url:
                    missing_alt[page_url] = missing_alt.get(page_url, 0) + 1
        except Exception as e:
            print(f'[SF] Erro ao processar CSV de imagens: {e}')

    results = []
    for row in rows:
        # Skip non-HTML resources
        ct = _col(row, 'Content Type', 'Content type', 'Type').lower()
        if ct and 'html' not in ct:
            continue

        url = _col(row, 'Address', 'address', 'URL')
        if not url or not url.startswith('http'):
            continue

        page, issues = _issues_from_sf_row(row)

        # Inject image alt issues from images CSV
        if url in missing_alt:
            n = missing_alt[url]
            issues.append({
                'category': 'image', 'severity': 'medium',
                'title': f'{n} imagem(ns) sem atributo alt',
                'description': 'O atributo alt é indexado pelo Google Images e melhora a acessibilidade.',
                'current_value': f'{n} imagem(ns) sem alt (fonte: Screaming Frog)',
                'suggestion': 'Adicione um alt descritivo em cada imagem com palavras-chave relevantes.'
            })

        results.append((page, issues))

    return results


# ---------------------------------------------------------------------------
# Screaming Frog CLI — execução local
# ---------------------------------------------------------------------------

def get_sf_cli_path():
    """Return SF CLI path from env or default Windows location."""
    return os.environ.get(
        'SF_CLI_PATH',
        r'C:\Program Files (x86)\Screaming Frog SEO Spider\ScreamingFrogSEOSpider.exe'
    )


def sf_cli_available():
    """Check if SF CLI executable exists."""
    return Path(get_sf_cli_path()).exists()


def crawl_with_sf_cli(url, output_dir=None, timeout=600):
    """
    Run Screaming Frog in headless/CLI mode and return parsed results.

    Args:
        url:        Target URL to crawl
        output_dir: Where SF saves the CSVs (temp dir if None)
        timeout:    Max seconds to wait for SF to finish

    Returns:
        list of (page_dict, issues_list)  — same format as process_sf_csv
    """
    sf_path = get_sf_cli_path()
    if not Path(sf_path).exists():
        raise FileNotFoundError(
            f'Screaming Frog não encontrado em: {sf_path}\n'
            'Configure SF_CLI_PATH no arquivo .env'
        )

    use_temp = output_dir is None
    if use_temp:
        output_dir = tempfile.mkdtemp(prefix='sf_export_')

    output_dir = str(Path(output_dir).resolve())

    cmd = [
        sf_path,
        '--crawl', url,
        '--headless',
        '--save-crawl',
        '--export-tabs', 'Internal:All,Images:Missing Alt Text',
        '--output-folder', output_dir,
        '--overwrite',
        '--timestamped-output', 'false',
    ]

    print(f'[SF CLI] Iniciando crawl: {url}')
    print(f'[SF CLI] Saída em: {output_dir}')

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise TimeoutError(f'Screaming Frog excedeu o tempo limite de {timeout}s')

    if proc.returncode not in (0, 1):   # SF sometimes returns 1 on warnings
        raise RuntimeError(
            f'Screaming Frog encerrou com código {proc.returncode}.\n'
            f'Saída: {stderr[:500]}'
        )

    # Find exported CSV files
    out_path = Path(output_dir)
    internal_csv_path = _find_csv(out_path, 'internal_all')
    images_csv_path   = _find_csv(out_path, 'images_missing_alt_text')

    if not internal_csv_path:
        raise FileNotFoundError(
            f'CSV "Internal:All" não encontrado em {output_dir}.\n'
            'Verifique se o SF exportou corretamente.'
        )

    internal_content = internal_csv_path.read_bytes()
    images_content   = images_csv_path.read_bytes() if images_csv_path else None

    results = process_sf_csv(internal_content, images_content)
    print(f'[SF CLI] Crawl concluído. {len(results)} páginas encontradas.')
    return results


def _find_csv(folder, keyword):
    """Find a CSV file in folder whose name contains keyword (case-insensitive)."""
    keyword = keyword.lower().replace(' ', '_').replace(':', '_')
    for f in sorted(folder.glob('*.csv')):
        if keyword in f.name.lower().replace(' ', '_').replace(':', '_'):
            return f
    return None
