"""Download and verify the pinned public model; no account, API key, or cloud inference."""
import hashlib
from pathlib import Path
import urllib.request

URL = 'https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf'
SHA256 = '00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4'


def prepare(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    target = root/'qwen35-4b-q4.gguf'
    def digest(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    if target.exists():
        if digest(target) != SHA256:
            raise ValueError('Existing model checksum differs; refusing to overwrite')
        return target
    pending = target.with_suffix('.download')
    with urllib.request.urlopen(URL, timeout=120) as response, pending.open('wb') as output:
        while chunk := response.read(8*1024*1024):
            output.write(chunk)
    if digest(pending) != SHA256:
        raise ValueError('Model checksum mismatch; not activating download')
    pending.replace(target)
    return target


if __name__ == '__main__':
    print(prepare(Path(__file__).resolve().parent.parent/'models'))
