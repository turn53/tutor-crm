"""Use Zoom's original speech transcript before its lossy AI summary."""
import re
import datetime
import urllib.parse
import urllib.request


def awaiting_transcript(payload, now=None, grace_minutes=60):
    """A transcript may appear after the video; don't prematurely settle for a summary."""
    start = datetime.datetime.fromisoformat(payload['start_time'].replace('Z','+00:00'))
    if start.tzinfo is None:
        raise ValueError('Для расшифровки нужно время занятия с часовым поясом')
    end = start+datetime.timedelta(minutes=float(payload.get('minutes') or 60))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now < end+datetime.timedelta(minutes=grace_minutes)


def transcript_text(vtt):
    lines = vtt.replace('\r', '').splitlines()
    turns, speaker, words = [], '', []
    def flush():
        if words:
            turns.append((speaker+': ' if speaker else '')+' '.join(words))
            words.clear()
    for index, line in enumerate(lines):
        line = line.strip()
        if not line or line == 'WEBVTT' or '-->' in line:
            continue
        if line.isdigit() and '-->' in next((x for x in lines[index+1:] if x.strip()), ''):
            continue
        line = re.sub(r'<[^>]*>', '', line)
        match = re.match(r'^([^:]{1,70}):\s*(.*)', line)
        if match:
            person, text = match.groups()
            if person != speaker or sum(map(len, words)) > 650:
                flush()
            speaker = person
            words.append(text)
        else:
            words.append(line)
    flush()
    return '\n\n'.join(turns)


def download_transcripts(info, token):
    # Imported lazily: video_sync uses zoom_sync for the same Zoom credentials.
    from video_sync import SafeRedirect, safe_url
    files = [f for f in info.get('recording_files', []) if f.get('recording_type') == 'audio_transcript']
    if any(f.get('status') != 'completed' for f in files):
        from reporting import ReportUnavailable
        raise ReportUnavailable('Zoom готовит расшифровку. Продолжим автоматически')
    texts = []
    for file in sorted(files, key=lambda f: f.get('recording_start', '')):
        url = file['download_url']
        safe_url(url)
        host = urllib.parse.urlsplit(url).hostname
        if not (host == 'zoom.us' or host.endswith('.zoom.us')):
            raise ValueError('Ожидался адрес расшифровки Zoom')
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer '+token})
        with urllib.request.build_opener(SafeRedirect()).open(request, timeout=120) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('Расшифровка превышает допустимый размер')
        text = raw.decode('utf-8-sig')
        if not text.lstrip().startswith('WEBVTT'):
            raise ValueError('Zoom ещё не предоставил текст расшифровки')
        texts.append(transcript_text(text))
    return '\n\n'.join(texts)
