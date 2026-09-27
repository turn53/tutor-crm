"""Deterministic draft reports: no model, subprocess or network calls."""
import re
from reporting import ReportUnavailable, validate_report

# Each card is narrow: mention of vectors alone must not add every vector topic.
CARDS = (
    ('Вектор', r'определени\w* вектор|поняти\w* вектор|вектор\w* как направленн|вектор\w* — направленн',
     'Вектор — направленный отрезок. Его начало и конец задают направление; длина вектора равна длине этого отрезка.'),
    ('Координаты вектора', r'координат\w*\s+(?:\w+\s+){0,2}вектор|вектор\w*\s+(?:\w+\s+){0,2}координат',
     'Для A(x₁, y₁), B(x₂, y₂): AB = (x₂ − x₁, y₂ − y₁). Из координат конца вычитают координаты начала.'),
    ('Длина вектора', r'(?:длин|модул|норм)\w*\s+(?:\w+\s+){0,2}вектор|вектор\w*\s+(?:\w+\s+){0,2}(?:длин|модул)',
     'Для a = (x, y): |a| = √(x² + y²). В пространстве для a = (x, y, z): |a| = √(x² + y² + z²).'),
    ('Скалярное произведение', r'скалярн\w*\s+произведен',
     'a · b = |a| |b| cos φ. На плоскости a · b = aₓbₓ + aᵧbᵧ. Для ненулевых векторов cos φ = (a · b) / (|a| |b|). Ненулевые векторы перпендикулярны, если их скалярное произведение равно нулю.'),
    ('Квадратные уравнения', r'квадратн\w*\s+уравнен|дискриминант',
     'ax² + bx + c = 0, a ≠ 0. D = b² − 4ac. При D > 0: x₁,₂ = (−b ± √D)/(2a); при D = 0: x = −b/(2a); при D < 0 действительных корней нет.'),
    ('Теорема Виета', r'теорем\w*\s+виет',
     'Для корней уравнения ax² + bx + c = 0, a ≠ 0: x₁ + x₂ = −b/a, x₁x₂ = c/a.'),
    ('Теорема Пифагора', r'теорем\w*\s+пифагор',
     'В прямоугольном треугольнике a² + b² = c², где a и b — катеты, c — гипотенуза.'),
    ('Сложение и вычитание векторов', r'(?:сложени|вычитани|добавлени)\w* (?:\w+\s+){0,3}вектор|операци\w* с вектор',
     'Если a = (aₓ, aᵧ), b = (bₓ, bᵧ), то a + b = (aₓ + bₓ, aᵧ + bᵧ), a − b = (aₓ − bₓ, aᵧ − bᵧ).'),
    ('Умножение вектора на число', r'умножени\w* (?:вектор\w* )?на (?:число|скаляр)',
     'Для a = (x, y): k a = (kx, ky), |k a| = |k| |a|. При k > 0 направление сохраняется, при k < 0 меняется на противоположное.'),
    ('Прямая, отрезок и луч', r'прям\w* лини|отрезк\w* и луч|различ\w* (?:\w+\s+){0,4}луч',
     'Прямая бесконечна в обе стороны. Отрезок — часть прямой между двумя точками, включая концы. Луч имеет начало и бесконечно продолжается в одном направлении.'),
    ('Углы', r'определени\w* угл|поняти\w* угл|прям\w* угл|туп\w* угл|остр\w* угл|угл\w* как геометрическ',
     'Угол образован двумя лучами с общим началом — вершиной. Острый угол: 0° < α < 90°; прямой: α = 90°; тупой: 90° < α < 180°; развёрнутый: α = 180°.'),
    ('Биссектриса угла', r'биссектрис',
     'Биссектриса угла — луч, исходящий из его вершины и делящий угол на два равных угла. Каждая часть равна половине исходного угла.'),
    ('Смежные и вертикальные углы', r'смежн\w* угл|вертикальн\w* угл',
     'Смежные углы имеют общую сторону, а две другие стороны — противоположные лучи. Их сумма равна 180°. Вертикальные углы образованы пересечением двух прямых и равны.'),
    ('Сокращение дробей', r'сокращени\w* дроб|сокраща\w* дроб|основн\w* свойств\w* дроб',
     'Числитель и знаменатель дроби можно умножить или разделить на одно и то же ненулевое число: a/b = (ak)/(bk), b ≠ 0, k ≠ 0.'),
    ('Сложение и вычитание дробей', r'(?:сложени|вычитани)\w* (?:обыкновенн\w* )?дроб',
     'Дроби приводят к общему знаменателю, затем складывают или вычитают числители: a/b ± c/d = (ad ± bc)/(bd), b ≠ 0, d ≠ 0. Результат сокращают.'),
    ('Умножение и деление дробей', r'(?:умножени|делени)\w* (?:обыкновенн\w* )?дроб',
     '(a/b) · (c/d) = ac/(bd), b ≠ 0, d ≠ 0. (a/b) : (c/d) = ad/(bc), дополнительно c ≠ 0.'),
    ('Проценты', r'процент',
     '1% = 1/100. p% от числа A: A · p/100. Если p% числа равны B, то число равно 100B/p (p ≠ 0). Доля B от A в процентах: (B/A) · 100% (A ≠ 0).'),
    ('Линейные уравнения', r'линейн\w* уравнен',
     'ax + b = 0. При a ≠ 0: x = −b/a. При a = 0 и b = 0 подходит любое x; при a = 0 и b ≠ 0 решений нет.'),
    ('Формулы сокращённого умножения', r'формул\w* сокращ[её]нн\w* умножени|квадрат суммы|квадрат разности|разность квадратов',
     '(a + b)² = a² + 2ab + b². (a − b)² = a² − 2ab + b². a² − b² = (a − b)(a + b).'),
    ('Арифметическая прогрессия', r'арифметическ\w* прогресси',
     'Разность d = aₙ₊₁ − aₙ постоянна. aₙ = a₁ + (n − 1)d. Сумма первых n членов: Sₙ = (a₁ + aₙ)n/2.'),
    ('Геометрическая прогрессия', r'геометрическ\w* прогресси',
     'bₙ = b₁qⁿ⁻¹, где q — знаменатель прогрессии. При q ≠ 1: Sₙ = b₁(qⁿ − 1)/(q − 1). При q = 1: Sₙ = nb₁.'),
)

VERSION = 'rules-2'

HEADINGS = {
    'тема': 'topic', 'тема занятия': 'topic', 'тема урока': 'topic',
    'домашнее задание': 'homework', 'домашняя работа': 'homework',
    'домашка': 'homework', 'дз': 'homework', 'д/з': 'homework', 'homework': 'homework',
    'следующий урок': 'next', 'план следующего урока': 'next',
}
# These sections can contain teacher errands and must never prove coverage.
ACTION_HEADINGS = {'следующие шаги', 'дальнейшие шаги', 'next steps'}
UNCERTAIN = r'неизвестн|не указан|не определ|не задан|не зафикс|уточни|возможно|может быть|если будет|планиру|позже|пообещал|выдать|определить и|подготовить'
FUTURE = r'\b(?:будем|предстоит|планиру\w*|следующ\w*|повторить|изучить)\b'
NEGATED = r'\bне\s+(?:\w+\s+){0,2}(?:разбира\w*|изуча\w*|проходи\w*|рассматрива\w*|обсужда\w*|реша\w*)'


def clean_line(line):
    # Keep visible task text, discard Zoom tracking URLs and markdown syntax.
    line = re.sub(r'\[([^]\n]+)\]\(https?://[^\s]*\)', r'\1', line)
    line = re.sub(r'https?://\S+', '', line)
    line = re.sub(r'^\s*(?:[-*•]\s+|\d+[.)]\s+)', '', line)
    return re.sub(r'^[#\s]+', '', line).replace('**', '').strip()


def source_lines(source):
    """Yield section role and visible text; preserve Zoom heading scope."""
    active = None
    action_level = None
    for raw in source.splitlines():
        raw = raw.strip()
        if not raw:
            continue  # Zoom inserts blank lines after every heading.
        line = clean_line(raw)
        heading = re.match(r'^(#{1,6})\s+', raw)
        level = len(heading[1]) if heading else None
        if action_level is not None and level and level <= action_level:
            action_level = None
        head, sep, body = line.partition(':')
        title = head.lower().strip()
        if title in ACTION_HEADINGS:
            action_level = level or 1
            active = 'action'
            continue
        if action_level is not None:
            if not heading:
                yield 'action', line
            continue
        key = HEADINGS.get(title)
        if key:
            active = key
            if sep and body.strip():
                yield key, body.strip()
            continue
        if heading or line.endswith(':') or re.match(r'^[А-ЯA-Z][^:]{0,60}:', line):
            active = None
        yield active or 'learning', line


def sections(source):
    result = {}
    for role, text in source_lines(source):
        if role in ('topic', 'homework', 'next'):
            result.setdefault(role, []).append(text)
    return {k: '\n'.join(v) for k, v in result.items()}


def assignment(text):
    """Conservative extraction, not a generic next-steps-to-homework mapping."""
    if re.search(UNCERTAIN + r'|\bне\s+(?:задал\w*|поручил\w*|нужно|надо|требуется|выполнять|решать)', text, re.I):
        return None
    patterns = (
        r'(?:выполнить|выполните|решить|решите|повторить|повторите)\s+домашн\w*\s+(?:задани\w*|работ\w*)\s*(.*)',
        r'(?:на дом\s+(?:задали|задано|задал|задала)|домашн\w*\s+(?:задани\w*|работ\w*)\s*[:—–-])\s*(.+)',
        r'(?:задал|задала|задали|поручил|поручила)\s+(?:учени\w*\s+)?(?:на дом\s+)?((?:решить|выполнить|повторить|выучить)\s+.+)',
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = match[1].strip(' :—–-')
            # Drop delivery instructions/person names after the task itself.
            value = re.split(r'[,;]\s*(?:отправ|пришл|сообщ)', value, maxsplit=1, flags=re.I)[0]
            if value and not re.search(r'\bне\s+(?:нужно|надо|требуется|выполнять|решать)', text, re.I):
                return value
    return None


def unique(values):
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


def analyze_source(source):
    """Internal evidence stays separate from the student's copyable notes."""
    if not isinstance(source, str) or not source.strip():
        raise ReportUnavailable('Zoom ещё не подготовил сводку урока')
    if len(source) > 120000:
        raise ReportUnavailable('Сводка слишком длинная для обработки по правилам')
    lines = list(source_lines(source))
    explicit = sections(source)
    homework = [text for role, text in lines if role == 'homework'
                and not re.search(UNCERTAIN, text, re.I)]
    for role, line in lines:
        if role not in ('homework', 'next'):
            extracted = assignment(line)
            if extracted:
                homework.append(extracted)
    evidence = {}
    for role, line in lines:
        if role not in ('learning', 'topic'):
            continue
        # Do not read pupil/teacher action items as material covered in class.
        if assignment(line) or re.search(r'домашн|на дом', line, re.I):
            continue
        for sentence in re.split(r'(?<=[.!?])\s+|;', line):
            if re.search(FUTURE + '|' + NEGATED, sentence, re.I):
                continue
            for title, pattern, _ in CARDS:
                if re.search(pattern, sentence, re.I):
                    evidence.setdefault(title, sentence)
    matches = [(title, content) for title, _, content in CARDS if title in evidence]
    warnings = []
    if not matches:
        warnings.append('В справочнике не найдена подтверждённая тема. Нужна ручная проверка.')
    if not homework:
        warnings.append('Конкретное домашнее задание не найдено в источнике.')
    return dict(version=VERSION, matches=matches, evidence=evidence, warnings=warnings,
                topic=explicit.get('topic'), homework='\n'.join(unique(homework)) or 'Не зафиксировано',
                next=explicit.get('next', 'Не указано'))


def rule_report(source):
    analysis = analyze_source(source)
    matches = analysis['matches']
    topic = analysis['topic'] or ('; '.join(t for t, _ in matches) if matches else 'Тема не определена')
    # This heading identifies reference theory, without claiming a verbatim lesson record.
    notes = 'Основные сведения по теме\n\n' + '\n\n'.join(
        title + '\n' + content for title, content in matches) if matches else 'Теоретические сведения не определены.'
    return validate_report(dict(topic=topic[:1000], notes=notes,
        understanding='Не зафиксировано', homework=analysis['homework'], next=analysis['next']))
