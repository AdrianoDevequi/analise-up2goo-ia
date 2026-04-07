import os
import re
import json
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# SEO Analysis
# ---------------------------------------------------------------------------

def analyze_page(page_data):
    """
    Analyze a single page for SEO issues.

    Returns:
        (issues: list[dict], word_count: int)
    """
    url = page_data['url']
    soup = page_data.get('soup')
    status_code = page_data.get('status_code', 0)
    issues = []

    # --- Inaccessible page ---
    if page_data.get('error') or status_code == 0:
        issues.append({
            'category': 'technical',
            'severity': 'high',
            'title': 'Página inacessível',
            'description': f'Não foi possível acessar esta página. Erro: {page_data.get("error", "desconhecido")}',
            'current_value': page_data.get('error', 'Erro desconhecido'),
            'suggestion': 'Verifique se a URL está correta e se o servidor está funcionando.'
        })
        return issues, 0

    if status_code >= 400:
        issues.append({
            'category': 'technical',
            'severity': 'high',
            'title': f'Erro HTTP {status_code}',
            'description': f'Esta página retornou o código de erro {status_code}.',
            'current_value': str(status_code),
            'suggestion': 'Corrija o erro ou redirecione para uma página válida com status 301.'
        })
        return issues, 0

    if soup is None:
        return issues, 0

    # --- TITLE ---
    title_tag = soup.find('title')
    title_text = title_tag.get_text().strip() if title_tag else ''
    title_len = len(title_text)

    if not title_text:
        issues.append({
            'category': 'title',
            'severity': 'high',
            'title': 'Tag <title> ausente ou vazia',
            'description': 'O título da página é o principal fator de SEO on-page. Aparece nos resultados do Google.',
            'current_value': '(sem título)',
            'suggestion': 'Adicione um título com 50–60 caracteres contendo a palavra-chave principal no início.'
        })
    elif title_len < 30:
        issues.append({
            'category': 'title',
            'severity': 'medium',
            'title': f'Título muito curto ({title_len} caracteres)',
            'description': 'Títulos curtos não aproveitam todo o espaço nos resultados de busca e perdem oportunidades de ranqueamento.',
            'current_value': title_text,
            'suggestion': 'Expanda o título para 50–60 caracteres incluindo palavras-chave relevantes e o nome da empresa.'
        })
    elif title_len > 60:
        issues.append({
            'category': 'title',
            'severity': 'medium',
            'title': f'Título muito longo ({title_len} caracteres)',
            'description': 'O Google trunca títulos com mais de ~60 caracteres nos resultados de busca.',
            'current_value': title_text,
            'suggestion': f'Reduza o título para no máximo 60 caracteres. Priorize as palavras-chave mais importantes.'
        })

    # --- META DESCRIPTION ---
    meta_el = soup.find('meta', attrs={'name': re.compile(r'^description$', re.I)})
    meta_text = meta_el.get('content', '').strip() if meta_el else ''
    meta_len = len(meta_text)

    if not meta_text:
        issues.append({
            'category': 'meta',
            'severity': 'high',
            'title': 'Meta description ausente',
            'description': 'A meta description aparece como resumo nos resultados do Google e influencia diretamente o CTR (taxa de cliques).',
            'current_value': '(sem meta description)',
            'suggestion': 'Adicione uma meta description com 150–160 caracteres que descreva o conteúdo da página de forma atrativa.'
        })
    elif meta_len < 120:
        issues.append({
            'category': 'meta',
            'severity': 'medium',
            'title': f'Meta description muito curta ({meta_len} caracteres)',
            'description': 'Uma meta description curta não aproveita o espaço disponível para atrair cliques.',
            'current_value': meta_text,
            'suggestion': 'Expanda a descrição para 150–160 caracteres com um texto persuasivo e call-to-action.'
        })
    elif meta_len > 160:
        issues.append({
            'category': 'meta',
            'severity': 'low',
            'title': f'Meta description muito longa ({meta_len} caracteres)',
            'description': 'O Google corta meta descriptions após ~160 caracteres, ocultando parte do texto.',
            'current_value': meta_text,
            'suggestion': 'Reduza para no máximo 160 caracteres, mantendo as informações mais importantes no início.'
        })

    # --- H1 ---
    h1_tags = soup.find_all('h1')
    h1_count = len(h1_tags)

    if h1_count == 0:
        issues.append({
            'category': 'heading',
            'severity': 'high',
            'title': 'Tag H1 ausente',
            'description': 'O H1 é o título principal visível da página. É essencial para informar ao Google o tema central do conteúdo.',
            'current_value': '(sem H1)',
            'suggestion': 'Adicione um H1 claro e descritivo com a palavra-chave principal. Deve haver apenas um H1 por página.'
        })
    elif h1_count > 1:
        h1_texts = [h.get_text().strip() for h in h1_tags]
        issues.append({
            'category': 'heading',
            'severity': 'medium',
            'title': f'Múltiplos H1 ({h1_count} encontrados)',
            'description': 'Múltiplos H1 confundem os buscadores sobre qual é o tema principal da página.',
            'current_value': ' | '.join(h1_texts[:3]),
            'suggestion': 'Mantenha apenas 1 tag H1 com o tema principal. Transforme os demais em H2 ou H3.'
        })

    # --- HEADINGS STRUCTURE ---
    h2_tags = soup.find_all('h2')
    if len(h2_tags) == 0 and h1_count > 0:
        issues.append({
            'category': 'heading',
            'severity': 'low',
            'title': 'Nenhum subtítulo H2 encontrado',
            'description': 'Subtítulos H2 organizam o conteúdo em seções, facilitando a leitura e o entendimento pelos buscadores.',
            'current_value': '0 subtítulos H2',
            'suggestion': 'Divida o conteúdo em seções com títulos H2 descritivos contendo palavras-chave secundárias.'
        })

    # --- CONTENT LENGTH ---
    body = soup.find('body')
    body_text = body.get_text(separator=' ', strip=True) if body else ''
    words = [w for w in body_text.split() if len(w) > 2]
    word_count = len(words)

    if 0 < word_count < 300:
        issues.append({
            'category': 'content',
            'severity': 'medium',
            'title': f'Conteúdo escasso ({word_count} palavras)',
            'description': 'Páginas com pouco texto têm dificuldade de ranquear. O mínimo recomendado é 300 palavras para páginas de serviço/produto.',
            'current_value': f'{word_count} palavras',
            'suggestion': 'Adicione mais conteúdo: descrição detalhada do produto/serviço, benefícios, perguntas frequentes, depoimentos.'
        })

    # --- IMAGES WITHOUT ALT ---
    all_images = soup.find_all('img')
    images_no_alt = [img for img in all_images if not img.get('alt', '').strip()]
    if images_no_alt:
        issues.append({
            'category': 'image',
            'severity': 'medium',
            'title': f'{len(images_no_alt)} imagem(ns) sem texto alternativo (alt)',
            'description': 'O atributo alt das imagens é indexado pelo Google Images e melhora a acessibilidade do site.',
            'current_value': f'{len(images_no_alt)} de {len(all_images)} imagens sem alt',
            'suggestion': 'Adicione um alt descritivo em cada imagem, incluindo palavras-chave relevantes quando fizer sentido.'
        })

    # --- OPEN GRAPH ---
    og_title = soup.find('meta', property='og:title') or soup.find('meta', attrs={'property': 'og:title'})
    og_desc = soup.find('meta', property='og:description') or soup.find('meta', attrs={'property': 'og:description'})
    og_image = soup.find('meta', property='og:image') or soup.find('meta', attrs={'property': 'og:image'})
    missing_og = []
    if not og_title:
        missing_og.append('og:title')
    if not og_desc:
        missing_og.append('og:description')
    if not og_image:
        missing_og.append('og:image')

    if missing_og:
        issues.append({
            'category': 'social',
            'severity': 'low',
            'title': 'Open Graph incompleto',
            'description': 'As tags Open Graph controlam como a página aparece ao ser compartilhada no Facebook, WhatsApp e outras redes.',
            'current_value': f'Faltando: {", ".join(missing_og)}',
            'suggestion': 'Adicione as tags Open Graph ausentes no <head> da página para melhorar o compartilhamento social.'
        })

    # --- CANONICAL ---
    canonical = soup.find('link', rel='canonical') or soup.find('link', attrs={'rel': 'canonical'})
    if not canonical:
        issues.append({
            'category': 'technical',
            'severity': 'low',
            'title': 'Tag canonical ausente',
            'description': 'A tag canonical indica ao Google a URL principal da página, evitando penalidades por conteúdo duplicado.',
            'current_value': '(sem canonical)',
            'suggestion': f'Adicione no <head>: <link rel="canonical" href="{url}">'
        })

    # --- LOAD TIME ---
    load_time = page_data.get('load_time', 0)
    if load_time > 3:
        issues.append({
            'category': 'technical',
            'severity': 'medium',
            'title': f'Carregamento lento ({load_time}s)',
            'description': 'Páginas lentas prejudicam a experiência do usuário e o ranqueamento. O Google recomenda menos de 3 segundos.',
            'current_value': f'{load_time} segundos',
            'suggestion': 'Otimize imagens, use cache, comprima arquivos CSS/JS e considere um CDN para melhorar a velocidade.'
        })

    # --- URL STRUCTURE ---
    path = urlparse(url).path
    if len(url) > 80:
        issues.append({
            'category': 'technical',
            'severity': 'low',
            'title': 'URL muito longa',
            'description': 'URLs longas são difíceis de compartilhar e podem ser truncadas nos resultados de busca.',
            'current_value': url,
            'suggestion': 'Use URLs curtas, descritivas, com palavras separadas por hífen. Ex: /produtos/nome-do-produto'
        })

    # --- INTERNAL LINKS ---
    base_domain = urlparse(url).netloc
    internal_links = [
        a for a in soup.find_all('a', href=True)
        if a['href'].startswith('/') or base_domain in a.get('href', '')
    ]
    if len(internal_links) < 3:
        issues.append({
            'category': 'links',
            'severity': 'low',
            'title': f'Poucos links internos ({len(internal_links)})',
            'description': 'Links internos ajudam o Google a navegar e entender a estrutura do site, distribuindo autoridade entre as páginas.',
            'current_value': f'{len(internal_links)} link(s) interno(s)',
            'suggestion': 'Adicione links para outras páginas relevantes do site: produtos relacionados, categorias, blog posts.'
        })

    return issues, word_count


# ---------------------------------------------------------------------------
# AI Suggestions via Claude
# ---------------------------------------------------------------------------

def get_ai_suggestions(page_data):
    """
    Use Claude API to generate specific text improvement suggestions for a page.

    Returns dict with suggested title, meta description, H1, and content tip.
    Returns None if API key is not configured.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return None

    soup = page_data.get('soup')
    if not soup:
        return None

    try:
        import anthropic

        title_tag = soup.find('title')
        title_text = title_tag.get_text().strip() if title_tag else ''

        meta_el = soup.find('meta', attrs={'name': re.compile(r'^description$', re.I)})
        meta_text = meta_el.get('content', '').strip() if meta_el else ''

        h1_tags = soup.find_all('h1')
        h1_text = h1_tags[0].get_text().strip() if h1_tags else ''

        h2_tags = soup.find_all('h2')
        h2_texts = [h.get_text().strip() for h in h2_tags[:5]]

        body = soup.find('body')
        body_text = body.get_text(separator=' ', strip=True)[:1500] if body else ''

        prompt = f"""Você é um especialista em SEO e copywriting. Analise os elementos desta página e forneça sugestões de melhoria otimizadas para SEO.

URL da página: {page_data['url']}

ELEMENTOS ATUAIS:
- Título: {title_text or '(ausente)'}
- Meta Description: {meta_text or '(ausente)'}
- H1: {h1_text or '(ausente)'}
- Subtítulos H2: {', '.join(h2_texts) if h2_texts else '(nenhum)'}
- Trecho do conteúdo: {body_text}

INSTRUÇÕES:
Retorne APENAS um JSON válido (sem markdown, sem explicações) com este formato exato:
{{
  "titulo": "Novo título otimizado com 50-60 caracteres",
  "meta_description": "Nova meta description persuasiva com 150-160 caracteres incluindo call-to-action",
  "h1": "Novo H1 principal otimizado",
  "dica_conteudo": "Sugestão específica e prática para melhorar ou expandir o conteúdo desta página",
  "palavras_chave": ["palavra1", "palavra2", "palavra3"]
}}

Use português brasileiro. Seja específico para o nicho/tema identificado na página."""

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=800,
            messages=[{'role': 'user', 'content': prompt}]
        )

        response_text = message.content[0].text.strip()

        # Extract JSON
        json_match = re.search(r'\{[\s\S]*\}', response_text)
        if json_match:
            return json.loads(json_match.group())

    except Exception as e:
        print(f'[AI] Erro ao gerar sugestões para {page_data.get("url")}: {e}')

    return None
