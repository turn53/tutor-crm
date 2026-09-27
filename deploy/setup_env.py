"""Run once on the VPS after Docker installation. Prompts do not echo passwords."""
import getpass
import os
from pathlib import Path
import re
import secrets
import subprocess

def main():
    root=Path(__file__).resolve().parent.parent
    target=root/'.env'
    if target.exists(): raise SystemExit('.env already exists; refusing to overwrite')
    domain=input('CRM domain (crm.example.ru): ').strip()
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',domain) or '.' not in domain: raise SystemExit('Enter an ASCII domain without https:// or paths')
    login=input('Login: ').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{3,40}',login): raise SystemExit('Use 3-40 letters, digits, underscore or dash')
    password=getpass.getpass('Password (at least 16 characters): ')
    if len(password)<16 or password!=getpass.getpass('Repeat password: '): raise SystemExit('Passwords differ or too short')
    result=subprocess.run(['docker','run','--rm','-i','caddy:2-alpine','caddy','hash-password','--algorithm','bcrypt'],
        input=password.encode('utf-8')+b'\n',capture_output=True,check=True)
    hashed=result.stdout.decode('ascii').strip()
    if not hashed.startswith('$2'): raise SystemExit('Could not generate bcrypt hash')
    target.write_text(f"CRM_DOMAIN={domain}\nCRM_LOGIN={login}\nCRM_PASSWORD_HASH='{hashed}'\nCRM_GATEWAY_SECRET={secrets.token_urlsafe(48)}\n",encoding='utf-8')
    target.chmod(0o600)
    data=root/'server-data';data.mkdir(exist_ok=True)
    if os.name!='nt':
        data.chmod(0o700)
        os.chown(data,10001,10001)
    print('Configuration saved. Import the migration data before starting CRM.')

if __name__=='__main__': main()
